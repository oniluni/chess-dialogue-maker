/** Transcription contracts between the UI and the local speech backend. */

export interface TranscriptLine {
  start: number;
  end: number;
  text: string;
}

export interface TranscriptionInfo {
  model: string;
  language: string;
}

export interface TranscriptionResponse {
  segments: TranscriptLine[];
  language: string;
  model: string;
}
