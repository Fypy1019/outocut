from __future__ import annotations

import asyncio
import json
import logging
import queue
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psutil

from .db import Database, utc_now
from .models import JobCreate, JobRecord, JobState, TtsRequest
from .providers import MiniMaxClient
from .renderer import FFmpegRenderer, RenderCancelled

logger = logging.getLogger("outocut_engine.jobs")


@dataclass(slots=True)
class QueuedJob:
    job_id: str
    request: JobCreate


@dataclass(slots=True, frozen=True)
class ResourceCheck:
    ready: bool
    reason: str = ""


MIN_FREE_DISK_BYTES = 2 * 1024**3
MIN_AVAILABLE_MEMORY_BYTES = 1024**3
MAX_CPU_PERCENT = 98.0
MAX_CONSECUTIVE_FAILURES = 3


def check_task_resources(request: JobCreate) -> ResourceCheck:
    """Check storage and system load immediately before a queued task starts."""
    output_dir = Path(request.output_dir).expanduser().resolve()
    cache_dir = Path(request.cache_dir).expanduser().resolve() if request.cache_dir else output_dir
    try:
        for label, directory in {"成片目录": output_dir, "缓存目录": cache_dir}.items():
            directory.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(directory).free < MIN_FREE_DISK_BYTES:
                return ResourceCheck(False, f"{label}可用空间不足 2 GB")
    except OSError as exc:
        return ResourceCheck(False, f"存储目录不可用：{exc}")

    if psutil.virtual_memory().available < MIN_AVAILABLE_MEMORY_BYTES:
        return ResourceCheck(False, "系统可用内存不足 1 GB")
    if psutil.cpu_percent(interval=0.2) >= MAX_CPU_PERCENT:
        return ResourceCheck(False, "系统 CPU 负载过高")
    return ResourceCheck(True)


class JobManager:
    def __init__(
        self,
        db: Database,
        renderer: FFmpegRenderer,
        provider_factory,
        resource_checker=check_task_resources,
    ):
        self.db = db
        self.renderer = renderer
        self.provider_factory = provider_factory
        self._queue: queue.Queue[QueuedJob | None] = queue.Queue()
        self._queue_lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._messages: dict[str, str] = {}
        self._resource_checker = resource_checker
        self._run_gate = threading.Event()
        self._run_gate.set()
        self._pause_lock = threading.Lock()
        self._pause_reason = ""
        self._pause_automatic = False
        self._consecutive_failures = 0
        self._worker = threading.Thread(target=self._work, name="outocut-render-worker", daemon=True)
        self._recover_interrupted_jobs()
        self._worker.start()

    def _recover_interrupted_jobs(self) -> None:
        now = utc_now()
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM jobs WHERE state IN (?, ?, ?)",
                (JobState.QUEUED, JobState.PREPARING_AUDIO, JobState.RENDERING),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["recovered"] = True
                connection.execute(
                    "UPDATE jobs SET state=?, error=?, payload=?, updated_at=? WHERE id=?",
                    (
                        JobState.FAILED,
                        "上次运行异常结束，可点击重试",
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        row["id"],
                    ),
                )
            connection.commit()

    def create(self, request: JobCreate) -> JobRecord:
        job_id = uuid4().hex
        now = utc_now()
        self.db.execute(
            """INSERT INTO jobs(id, state, progress, payload, created_at, updated_at)
               VALUES(?, ?, 0, ?, ?, ?)""",
            (job_id, JobState.DRAFT, request.model_dump_json(), now, now),
        )
        self._cancel_events[job_id] = threading.Event()
        self._messages[job_id] = "待合成"
        return self.get(job_id)

    def start(self, job_id: str) -> JobRecord:
        with self._queue_lock:
            record = self.get(job_id)
            if record.state != JobState.DRAFT:
                raise ValueError("只有待合成任务可以开始合成")
            event = threading.Event()
            self._cancel_events[job_id] = event
            self._set_state(job_id, JobState.QUEUED, 0, error=None, output_path=None)
            self._messages[job_id] = "等待进入合成队列"
            self._queue.put(QueuedJob(job_id, record.request))
        return self.get(job_id)

    def start_all(self) -> int:
        job_ids = [
            row["id"]
            for row in self.db.fetch_all(
                "SELECT id FROM jobs WHERE state=? ORDER BY created_at",
                (JobState.DRAFT,),
            )
        ]
        started = 0
        for job_id in job_ids:
            try:
                self.start(job_id)
                started += 1
            except ValueError:
                continue
        return started

    def queue_status(self) -> dict:
        return {
            "paused": not self._run_gate.is_set(),
            "automatic": self._pause_automatic,
            "reason": self._pause_reason,
            "consecutive_failures": self._consecutive_failures,
        }

    def pause_queue(self, reason: str = "队列已手动暂停", *, automatic: bool = False) -> dict:
        with self._pause_lock:
            self._pause_reason = reason
            self._pause_automatic = automatic
            self._run_gate.clear()
        return self.queue_status()

    def resume_queue(self) -> dict:
        with self._pause_lock:
            self._pause_reason = ""
            self._pause_automatic = False
            self._consecutive_failures = 0
            self._run_gate.set()
        return self.queue_status()

    def _wait_until_ready(self, job_id: str, request: JobCreate, cancel: threading.Event) -> bool:
        while not cancel.is_set():
            if not self._run_gate.wait(timeout=0.25):
                continue
            if cancel.is_set():
                return False
            resource = self._resource_checker(request)
            if not resource.ready:
                reason = f"资源不足，队列已自动暂停：{resource.reason}"
                logger.warning("任务 %s 资源检查未通过：%s", job_id, resource.reason)
                self._messages[job_id] = reason
                self.pause_queue(reason, automatic=True)
                continue
            if self._run_gate.is_set():
                return True
        return False

    def get(self, job_id: str) -> JobRecord:
        row = self.db.fetch_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise KeyError(job_id)
        request = JobCreate.model_validate_json(row["payload"])
        return JobRecord(
            id=row["id"],
            name=request.name,
            state=row["state"],
            progress=row["progress"],
            error=row["error"],
            output_path=row["output_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            request=request,
        )

    def list(self) -> list[dict]:
        records: list[dict] = []
        for row in self.db.fetch_all("SELECT * FROM jobs ORDER BY created_at DESC"):
            request = JobCreate.model_validate_json(row["payload"])
            records.append(
                {
                    "id": row["id"],
                    "name": request.name,
                    "template_name": request.template.name,
                    "shot_count": len(request.shots),
                    "state": row["state"],
                    "progress": row["progress"],
                    "message": self._messages.get(
                        row["id"], "待合成" if row["state"] == JobState.DRAFT else ""
                    ),
                    "error": row["error"],
                    "output_path": row["output_path"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return records

    def delete(self, job_id: str) -> None:
        record = self.get(job_id)
        if record.state not in {
            JobState.DRAFT,
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        }:
            raise ValueError("进行中的任务不能删除，请先取消任务")
        self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def cancel(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record.state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}:
            return record
        event = self._cancel_events.setdefault(job_id, threading.Event())
        event.set()
        self._set_state(job_id, JobState.CANCELLED, record.progress, error="任务已取消")
        return self.get(job_id)

    def retry(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record.state not in {JobState.FAILED, JobState.CANCELLED}:
            raise ValueError("只有失败或已取消任务可以重试")
        event = threading.Event()
        self._cancel_events[job_id] = event
        self._set_state(job_id, JobState.QUEUED, 0, error=None, output_path=None)
        self._messages[job_id] = "等待重试"
        self._queue.put(QueuedJob(job_id, record.request))
        return self.get(job_id)

    def delete_completed(self) -> int:
        with self.db.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE state=?",
                (JobState.SUCCEEDED,),
            )
            connection.commit()
            return cursor.rowcount

    def _set_state(
        self,
        job_id: str,
        state: JobState,
        progress: float,
        *,
        error: str | None = None,
        output_path: str | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE jobs SET state=?, progress=?, error=?, output_path=?, updated_at=? WHERE id=?",
            (state, max(0, min(progress, 1)), error, output_path, utc_now(), job_id),
        )

    def _prepare_audio(self, job_id: str, request: JobCreate, cancel: threading.Event) -> None:
        if not request.template.voice_enabled:
            return
        voice_id = request.template.voice_id
        if not voice_id:
            raise RuntimeError("智能配音已开启，但任务快照中没有 MiniMax 音色 ID")
        pending = [
            index for index, shot in enumerate(request.shots) if shot.text.strip() and not shot.voice_path
        ]
        if not pending:
            return
        client: MiniMaxClient = self.provider_factory("minimax")
        cache_root = (
            Path(request.cache_dir).expanduser().resolve()
            if request.cache_dir
            else self.renderer.settings.data_root / "temp"
        )
        voice_dir = cache_root / "minimax音频" / job_id
        for completed, index in enumerate(pending, start=1):
            if cancel.is_set():
                raise RenderCancelled("任务已取消")
            output = voice_dir / f"shot-{index + 1:03d}.mp3"
            request.shots[index].voice_path = str(
                asyncio.run(
                    client.tts(
                        TtsRequest(
                            text=request.shots[index].text,
                            voice_id=voice_id,
                            model=request.template.output and "speech-2.8-hd",
                            speed=request.template.voice_speed,
                        ),
                        output,
                    )
                )
            )
            self._messages[job_id] = f"正在生成配音 {completed}/{len(pending)}"
            self._set_state(job_id, JobState.PREPARING_AUDIO, completed / max(1, len(pending)) * 0.15)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, request = item.job_id, item.request
            cancel = self._cancel_events.setdefault(job_id, threading.Event())
            if cancel.is_set():
                self._queue.task_done()
                continue
            try:
                if not self._wait_until_ready(job_id, request, cancel):
                    continue
                self._set_state(job_id, JobState.PREPARING_AUDIO, 0)
                self._prepare_audio(job_id, request, cancel)
                output_paths: list[str] = []
                for output_index in range(request.count):
                    if cancel.is_set():
                        raise RenderCancelled("任务已取消")
                    self._set_state(job_id, JobState.RENDERING, output_index / request.count)

                    def update(
                        render_progress: float,
                        message: str,
                        current_index: int = output_index,
                        total: int = request.count,
                        current_job: str = job_id,
                    ) -> None:
                        overall = (current_index + render_progress) / total
                        self._messages[current_job] = message
                        self._set_state(current_job, JobState.RENDERING, 0.15 + overall * 0.84)

                    artifact = self.renderer.render(job_id, request, output_index, cancel, update)
                    if cancel.is_set():
                        raise RenderCancelled("任务已取消")
                    output_paths.append(str(artifact.video_path))
                if cancel.is_set():
                    raise RenderCancelled("任务已取消")
                self._messages[job_id] = f"已生成 {len(output_paths)} 个成片"
                self._set_state(job_id, JobState.SUCCEEDED, 1, output_path=output_paths[-1])
                logger.info("任务 %s（模板「%s」）合成完成，共 %d 个成片",
                               job_id, request.name, len(output_paths))
                self._consecutive_failures = 0
            except RenderCancelled as exc:
                self._messages[job_id] = str(exc)
                self._set_state(job_id, JobState.CANCELLED, 0, error=str(exc))
                logger.warning("任务 %s 已取消：%s", job_id, exc)
            except Exception as exc:  # noqa: BLE001 - task boundary must persist all failures
                self._messages[job_id] = "合成失败"
                self._set_state(job_id, JobState.FAILED, 0, error=str(exc)[-4000:])
                logger.error("视频合成失败（任务 %s，模板「%s」）：%s",
                             job_id, request.name, exc, exc_info=True)
                self._consecutive_failures += 1
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self.pause_queue(
                        f"连续 {self._consecutive_failures} 个任务失败，队列已自动暂停",
                        automatic=True,
                    )
                    logger.error("队列已自动暂停：连续 %d 个任务失败", self._consecutive_failures)
            finally:
                self._queue.task_done()
