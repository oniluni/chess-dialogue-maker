import { ALL_FORMATS, BlobSource, Input } from "mediabunny";
import type { InputAudioTrack, InputVideoTrack } from "mediabunny";

export interface MediaInfo {
  duration: number;
  width: number;
  height: number;
  hasAudio: boolean;
}

export interface OpenedMedia {
  input: Input;
  video: InputVideoTrack;
  audio: InputAudioTrack | null;
  info: MediaInfo;
}

/** Opens a local file and reads what the player and the exporter need. */
export async function openMedia(file: File): Promise<OpenedMedia> {
  const input = new Input({ formats: ALL_FORMATS, source: new BlobSource(file) });
  try {
    const duration = await input.computeDuration();
    if (!Number.isFinite(duration) || duration <= 0) {
      throw new Error("the file has no measurable duration");
    }

    const video = await input.getPrimaryVideoTrack();
    if (video === null) throw new Error("the file has no video track");
    if (!(await video.canDecode())) throw new Error("this browser cannot decode the video track");

    const audio = await input.getPrimaryAudioTrack();
    if (audio !== null && !(await audio.canDecode())) {
      throw new Error("this browser cannot decode the audio track");
    }

    const width = await video.getDisplayWidth();
    const height = await video.getDisplayHeight();
    if (width <= 0 || height <= 0) throw new Error("the video track has no frame size");

    return { input, video, audio, info: { duration, width, height, hasAudio: audio !== null } };
  } catch (error) {
    input.dispose();
    throw error;
  }
}
