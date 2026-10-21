/** One sound the model heard in the clip. */
export interface SoundDetection {
  kind: string;
  label: string;
  start: number;
  end: number;
  peak: number;
  reliable: boolean;
}

export interface SoundsResponse {
  events: SoundDetection[];
  model: string;
}

/** The dialogue lines as the prosody check needs them: times and the diarized voice. */
export interface ProsodyRequest {
  /** 16 kHz mono WAV, base64. */
  wav: string;
  segments: Array<{ start: number; end: number; label: "A" | "B" | null }>;
}

export interface ProsodyResponse {
  events: SoundDetection[];
  samples: Record<string, number>;
}
