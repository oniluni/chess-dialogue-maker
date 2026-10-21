/** Who-speaks hints produced by the local voice model. */

export interface SpeakerHint {
  label: "A" | "B" | null;
  /** 0..1 — how sure the model is about the label. */
  confidence: number;
}

export interface DiarizationResponse {
  hints: SpeakerHint[];
  /** Reliability of the two-voice split, 0..1. */
  reliability: number;
}

export interface DiarizationRequest {
  wav: string;
  segments: Array<{ start: number; end: number }>;
}
