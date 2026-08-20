from __future__ import annotations

import json
import logging
import threading
from typing import Any, Literal
from uuid import uuid4

from .db import Database, utc_now
from .models import OverlayCompositeRequest, ResolutionConvertRequest
from .overlays import OverlayProcessingCancelled, OverlayProcessor
from .resolution import ResolutionProcessor

logger = logging.getLogger("outocut_engine.media_batches")

BatchKind = Literal["overlay", "resolution"]
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"


class MediaBatchManager:
    def __init__(
        self,
        db: Database,
        overlay_processor: OverlayProcessor,
        resolution_processor: ResolutionProcessor,
        worker_count: int = 4,
    ):
        self.db = db
        self.overlay_processor = overlay_processor
        self.resolution_processor = resolution_processor
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._recover_interrupted_tasks()
        self._workers = [
            threading.Thread(
                target=self._work,
                name=f"outocut-media-batch-{index + 1}",
                daemon=True,
            )
            for index in range(worker_count)
        ]
        for worker in self._workers:
            worker.start()

    def _recover_interrupted_tasks(self) -> None:
        now = utc_now()
        with self.db.connection() as connection:
            connection.execute(
                """UPDATE media_tasks SET state='pending', error=NULL, updated_at=?
                   WHERE state='processing'""",
                (now,),
            )
            connection.execute(
                """UPDATE media_batches SET state='completed', updated_at=?
                   WHERE state='running' AND NOT EXISTS (
                       SELECT 1 FROM media_tasks
                       WHERE media_tasks.batch_id=media_batches.id
                       AND state IN ('pending', 'processing')
                   )""",
                (now,),
            )
            connection.commit()

    def create(self, kind: BatchKind, items: list[dict[str, Any]]) -> dict[str, Any]:
        batch_id = uuid4().hex
        now = utc_now()
        with self.db.connection() as connection:
            connection.execute(
                "INSERT INTO media_batches(id, kind, state, created_at, updated_at) VALUES(?,?,?,?,?)",
                (batch_id, kind, RUNNING, now, now),
            )
            connection.executemany(
                """INSERT INTO media_tasks(
                       id, batch_id, position, state, payload, created_at, updated_at
                   ) VALUES(?,?,?,'pending',?,?,?)""",
                [
                    (
                        uuid4().hex,
                        batch_id,
                        position,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    )
                    for position, payload in enumerate(items)
                ],
            )
            connection.commit()
        self._wake()
        return self.get(batch_id)

    def get(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.fetch_one("SELECT * FROM media_batches WHERE id=?", (batch_id,))
        if not batch:
            raise KeyError(batch_id)
        tasks = self.db.fetch_all(
            "SELECT * FROM media_tasks WHERE batch_id=? ORDER BY position", (batch_id,)
        )
        return {
            "id": batch["id"],
            "kind": batch["kind"],
            "state": batch["state"],
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
            "tasks": [self._task_dict(task) for task in tasks],
        }

    def latest(self, kind: BatchKind) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            "SELECT id FROM media_batches WHERE kind=? ORDER BY created_at DESC LIMIT 1",
            (kind,),
        )
        return self.get(row["id"]) if row else None

    def pause(self, batch_id: str) -> dict[str, Any]:
        batch = self.get(batch_id)
        if batch["state"] == COMPLETED:
            return batch
        self.db.execute(
            "UPDATE media_batches SET state=?, updated_at=? WHERE id=?",
            (PAUSED, utc_now(), batch_id),
        )
        if batch["kind"] == "overlay":
            for task in batch["tasks"]:
                if task["state"] == "processing":
                    self.overlay_processor.cancel(task["id"])
        if not self.db.fetch_one(
            """SELECT 1 FROM media_tasks
               WHERE batch_id=? AND state IN ('pending','processing') LIMIT 1""",
            (batch_id,),
        ):
            self.db.execute(
                "UPDATE media_batches SET state=?, updated_at=? WHERE id=?",
                (COMPLETED, utc_now(), batch_id),
            )
        self._wake()
        return self.get(batch_id)

    def resume(self, batch_id: str) -> dict[str, Any]:
        batch = self.get(batch_id)
        if batch["state"] != PAUSED:
            return batch
        if not any(task["state"] in {"pending", "processing"} for task in batch["tasks"]):
            self.db.execute(
                "UPDATE media_batches SET state=?, updated_at=? WHERE id=?",
                (COMPLETED, utc_now(), batch_id),
            )
            return self.get(batch_id)
        self.db.execute(
            "UPDATE media_batches SET state=?, updated_at=? WHERE id=?",
            (RUNNING, utc_now(), batch_id),
        )
        self._wake()
        return self.get(batch_id)

    def delete(self, batch_id: str) -> None:
        batch = self.get(batch_id)
        if batch["state"] == RUNNING:
            raise ValueError("运行中的媒体批次不能删除，请先暂停")
        self.db.execute("DELETE FROM media_batches WHERE id=?", (batch_id,))

    def delete_task(self, batch_id: str, task_id: str) -> None:
        batch = self.get(batch_id)
        if batch["state"] == RUNNING:
            raise ValueError("运行中的媒体批次不能删除子任务，请先暂停")
        task = self.db.fetch_one(
            "SELECT id FROM media_tasks WHERE id=? AND batch_id=?", (task_id, batch_id)
        )
        if not task:
            raise KeyError(task_id)
        self.db.execute("DELETE FROM media_tasks WHERE id=?", (task_id,))
        if not self.db.fetch_one("SELECT 1 FROM media_tasks WHERE batch_id=?", (batch_id,)):
            self.db.execute("DELETE FROM media_batches WHERE id=?", (batch_id,))

    @staticmethod
    def _task_dict(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "position": row["position"],
            "state": row["state"],
            "payload": json.loads(row["payload"]),
            "output_path": row["output_path"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _claim(self):
        with self.db.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT t.*, b.kind
                   FROM media_tasks t
                   JOIN media_batches b ON b.id=t.batch_id
                   WHERE t.state='pending' AND b.state='running'
                   AND NOT EXISTS (
                       SELECT 1 FROM media_tasks active
                       WHERE active.batch_id=t.batch_id AND active.state='processing'
                   )
                   ORDER BY b.created_at, t.position
                   LIMIT 1"""
            ).fetchone()
            if row:
                now = utc_now()
                connection.execute(
                    "UPDATE media_tasks SET state='processing', error=NULL, updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                connection.execute(
                    "UPDATE media_batches SET updated_at=? WHERE id=?", (now, row["batch_id"])
                )
            connection.commit()
            return row

    def _finish(
        self,
        task_id: str,
        batch_id: str,
        state: str,
        *,
        output_path: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self.db.connection() as connection:
            batch = connection.execute(
                "SELECT state FROM media_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if not batch:
                return
            if state == "cancelled" and batch["state"] == PAUSED:
                state = "pending"
                error = None
            connection.execute(
                """UPDATE media_tasks
                   SET state=?, output_path=?, error=?, updated_at=? WHERE id=?""",
                (state, output_path, error, now, task_id),
            )
            remaining = connection.execute(
                """SELECT 1 FROM media_tasks
                   WHERE batch_id=? AND state IN ('pending','processing') LIMIT 1""",
                (batch_id,),
            ).fetchone()
            if not remaining:
                connection.execute(
                    "UPDATE media_batches SET state=?, updated_at=? WHERE id=?",
                    (COMPLETED, now, batch_id),
                )
            else:
                connection.execute(
                    "UPDATE media_batches SET updated_at=? WHERE id=?", (now, batch_id)
                )
            connection.commit()
        self._wake()

    def _work(self) -> None:
        while not self._stop.is_set():
            task = self._claim()
            if task is None:
                with self._condition:
                    self._condition.wait(timeout=0.5)
                continue
            payload = json.loads(task["payload"])
            try:
                if task["kind"] == "overlay":
                    payload["operation_id"] = task["id"]
                    result = self.overlay_processor.render_composite(
                        OverlayCompositeRequest.model_validate(payload)
                    )
                else:
                    result = self.resolution_processor.convert(
                        ResolutionConvertRequest.model_validate(payload)
                    )
                self._finish(
                    task["id"],
                    task["batch_id"],
                    "succeeded",
                    output_path=str(result["output_path"]),
                )
            except OverlayProcessingCancelled:
                self._finish(task["id"], task["batch_id"], "cancelled")
            except Exception as exc:  # noqa: BLE001 - persistent task boundary
                logger.error("媒体子任务 %s 失败：%s", task["id"], exc, exc_info=True)
                self._finish(
                    task["id"], task["batch_id"], "failed", error=str(exc)[-4000:]
                )

    def close(self) -> None:
        self._stop.set()
        self._wake()
        for worker in self._workers:
            worker.join(timeout=1)
