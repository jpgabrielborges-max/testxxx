import React from 'react';
import {Composition} from 'remotion';
import {CinematicStory} from './CinematicStory';
import {demo} from './demo';
import type {CinematicProps} from './types';

export const Root:React.FC=()=> <Composition
  id="Cinematic25D"
  component={CinematicStory}
  fps={30}
  width={720}
  height={1280}
  durationInFrames={450}
  defaultProps={demo}
  calculateMetadata={({props})=>({durationInFrames:Math.max(30,Math.ceil((props as CinematicProps).story.duration*30))})}
/>;
