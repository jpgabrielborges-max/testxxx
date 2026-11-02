import type { CinematicProps } from './types';

export const demo: CinematicProps = {
  audio: null,
  story: {
    version: 1, fps: 30, duration: 15, environment: 'train_night',
    actors: [
      { id: 'actor_0', name: 'Lia', palette: { skin: '#efbc91', hair: '#b58150', clothes: '#d65a55' } },
      { id: 'actor_1', name: 'Caio', palette: { skin: '#dfad87', hair: '#302d35', clothes: '#4c9fb4' } }
    ],
    beats: [
      { start: 0, end: 4, text: 'O trem atravessava as montanhas naquela noite.', action: 'look', actors: ['actor_0'] },
      { start: 4, end: 8, text: 'Lia percebeu alguma coisa na floresta.', action: 'fear', actors: ['actor_0'] },
      { start: 8, end: 12, text: 'Caio levantou a lanterna.', action: 'lantern', actors: ['actor_1'] },
      { start: 12, end: 15, text: 'Mas a luz revelou uma surpresa.', action: 'look', actors: ['actor_0'] }
    ],
    events: [
      { actor: 'actor_0', action: 'look', at: 1, duration: 1.5 },
      { actor: 'actor_0', action: 'fear', at: 4.7, duration: 2.2 },
      { actor: 'actor_1', action: 'lantern', at: 8.2, duration: 3.5 },
      { actor: 'actor_0', action: 'look', at: 12.4, duration: 2.0 }
    ],
    camera: [
      { at: 0, position: [0, 1.05, 16.3], lookAt: [0, .4, 0], fov: 37 },
      { at: 5, position: [-1.2, .95, 12.7], lookAt: [-.45, .4, 0], fov: 36 },
      { at: 11, position: [1.1, 1.05, 11.4], lookAt: [.4, .47, 0], fov: 35 },
      { at: 15, position: [.45, 1.0, 13.2], lookAt: [0, .48, 0], fov: 36 }
    ],
    subtitles: [
      { start: 0, end: 4, text: 'O trem atravessava as montanhas naquela noite.' },
      { start: 4, end: 8, text: 'Lia percebeu alguma coisa na floresta.' },
      { start: 8, end: 12, text: 'Caio levantou a lanterna.' },
      { start: 12, end: 15, text: 'Mas a luz revelou uma surpresa.' }
    ]
  }
};
