import React from 'react';
import {AbsoluteFill, Audio, staticFile, useCurrentFrame} from 'remotion';
import {ThreeCanvas} from '@remotion/three';
import {World} from './World';
import type {CinematicProps} from './types';

export const CinematicStory:React.FC<CinematicProps>=({story,audio})=>{
  const t=useCurrentFrame()/story.fps;
  const subtitle=story.subtitles.find((s)=>t>=s.start&&t<s.end)?.text??'';
  return <AbsoluteFill style={{background:'#0d2137'}}>
    <ThreeCanvas width={720} height={1280} camera={{fov:37,near:.1,far:250,position:[0,1,16]}}
      gl={{antialias:true,powerPreference:'high-performance'}} shadows>
      <World story={story}/>
    </ThreeCanvas>
    <AbsoluteFill style={{justifyContent:'flex-end',alignItems:'center',paddingBottom:145,pointerEvents:'none'}}>
      <div style={{fontFamily:'Arial, sans-serif',fontWeight:900,fontSize:42,lineHeight:1.16,
        maxWidth:'86%',color:'#fff',textAlign:'center',WebkitTextStroke:'1.2px #191823',
        textShadow:'0 3px 5px #10131c',whiteSpace:'pre-line'}}>{subtitle}</div>
    </AbsoluteFill>
    {audio?<Audio src={staticFile(audio)}/>:null}
  </AbsoluteFill>;
};
