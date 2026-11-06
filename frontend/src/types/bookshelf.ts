export interface BookItem {
  id: string;
  title: string;
  coverUrl: string;
  createdAt: string;
  isReading: boolean;
}

export interface ReadingProgress {
  progress: number; // 0-100
  lastReadAt: string; // ISO date string
}
