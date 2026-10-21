/** Faces and people found in the clip's frames. */

export interface FrameFaces {
  time: number;
  boxes: Array<{ x: number; y: number; width: number; height: number; person: number }>;
}

export interface PersonInfo {
  id: number;
  frames: number;
  share: number;
  preview: { time: number; x: number; y: number; width: number; height: number };
}

export interface FacesResult {
  persons: PersonInfo[];
  frames: FrameFaces[];
}

export interface FacesRequest {
  frames: Array<{ time: number; width: number; height: number; rgb: string }>;
}
