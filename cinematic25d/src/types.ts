export type V3 = [number, number, number];
export type Act = 'look' | 'fear' | 'lantern' | 'walk' | 'run' | 'reach' | 'talk';
export type Actor = { id: string; name: string; palette: { skin: string; hair: string; clothes: string } };
export type Event = { actor: string; action: Act; at: number; duration: number };
export type CameraKey = { at: number; position: V3; lookAt: V3; fov: number };
export type Subtitle = { start: number; end: number; text: string };
export type Story = {
  version: number;
  fps: number;
  duration: number;
  environment: 'train_night' | 'forest_night' | 'town_day';
  actors: Actor[];
  beats: Array<{ start: number; end: number; text: string; action: Act; actors: string[] }>;
  events: Event[];
  camera: CameraKey[];
  subtitles: Subtitle[];
};
export type CinematicProps = { story: Story; audio: string | null };
