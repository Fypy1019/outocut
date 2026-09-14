export interface Health {
  status: string
  version: string
  data_root: string
  ffmpeg: boolean
  ffmpeg_version: string
  nvenc: boolean
  disk_free_bytes: number
}

export interface AssetCategory {
  id: string
  name: string
  directory: string
  scanned_at: string | null
  clip_count: number
  issue_count: number
}

export interface AssetFolderView extends AssetCategory {
  usable_count: number
  unsupported_file_count: number
  hdr_count: number
  preview_paths: string[]
}

export interface AssetRootOverview {
  root_directory: string
  folders: AssetFolderView[]
  folder_count: number
  usable_clip_count: number
  unsupported_file_count: number
  insufficient_folder_count: number
  scanned_at: string | null
}

export interface AssetClip {
  id: string
  category_id: string
  path: string
  name: string
  duration: number
  width: number
  height: number
  fps: number
  codec: string
  has_audio: boolean
  hdr: boolean
  issue: 'none' | 'missing' | 'unsupported' | 'corrupt' | 'hdr' | 'too_short'
  issue_message: string
}

export interface PersonaProfile {
  id: string
  name: string
  industry: string
  audience: string
  selling_points: string
  experience: string
  tone: string
  prohibited_claims: string[]
}

export interface OutputSettings {
  width: number
  height: number
  fps: number
  codec: 'libx264' | 'h264_nvenc'
  quality: number
}

export interface SubtitleSettings {
  enabled: boolean
  font_name: string
  font_path?: string | null
  font_size: number
  primary_color: string
  outline_color: string
  outline_width: number
  margin_v: number
  animation: 'none' | 'fade'
}

export interface TitleSettings {
  enabled: boolean
  start_time: number
  end_time: number
  font_name: string
  font_path?: string | null
  font_size: number
  primary_color: string
  centered: boolean
  outline_width: number
  outline_color: string
  shadow_color: string
  shadow_x: number
  shadow_y: number
}

export interface TemplateShot {
  id: string
  original_text: string
  rewritten_text: string
  duration: number
  category_id: string
  muted: boolean
}

export interface MixTemplate {
  id: string
  name: string
  shot_count: number
  category_ids: string[]
  default_shot_duration: number
  title: string
  subtitle: string
  background_music?: string | null
  background_music_enabled: boolean
  background_music_directory: string
  background_music_mode: 'random' | 'fixed'
  background_music_volume: number
  voice_enabled: boolean
  voice_id?: string | null
  voice_name: string
  voice_speed: number
  voice_volume: number
  reference_text: string
  copy_style: string
  shots: TemplateShot[]
  output: OutputSettings
  captions: SubtitleSettings
  title_settings: TitleSettings
}

export interface VoiceProfile {
  voice_id: string
  voice_name: string
  type: 'system_voice' | 'voice_cloning' | 'voice_generation'
  description: string[]
  created_time: string
  preview_path?: string | null
  saved_locally: boolean
}

export interface VoiceCloneResult {
  voice: VoiceProfile
  preview_error: string
}

export interface JobSummary {
  id: string
  name: string
  template_name: string
  shot_count: number
  state: 'draft' | 'queued' | 'preparing_audio' | 'rendering' | 'succeeded' | 'failed' | 'cancelled'
  progress: number
  message: string
  error?: string | null
  output_path?: string | null
  created_at: string
  updated_at: string
}

export interface JobQueueStatus {
  paused: boolean
  automatic: boolean
  reason: string
  consecutive_failures: number
}

export interface AppSettings {
  output_directory: string
  cache_directory: string
  asset_root_directory: string
  max_concurrent_jobs: number
  default_width: number
  default_height: number
  default_fps: number
  default_codec: 'libx264' | 'h264_nvenc'
  default_quality: number
  bailian_base_url: string
  ecom_text_api_mode: 'chat-completions' | 'responses' | 'claude'
  ecom_text_base_url: string
  ecom_text_model: string
  ecom_text_timeout_seconds: number
  ecom_image_api_mode: 'images' | 'responses'
  ecom_image_base_url: string
  ecom_image_model: string
  ecom_search_enabled: boolean
  minimax_base_url: string
  deepseek_base_url: string
  default_model: string
  rewrite_provider: 'bailian' | 'deepseek'
  default_voice_model: string
  asr_provider: 'bailian' | 'local'
  asr_model: string
  rewrite_instructions: string
  configured: Record<string, boolean>
}
