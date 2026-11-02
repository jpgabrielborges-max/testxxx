import React, {useMemo} from 'react';
import {useThree} from '@react-three/fiber';
import {useCurrentFrame} from 'remotion';
import type {CameraKey, Story, V3} from './types';
import {ActorRig} from './Actors';

const mix = (a: number, b: number, p: number) => a + (b-a)*p;
const smooth = (x: number) => {const t=Math.max(0,Math.min(1,x));return t*t*(3-2*t);};
function cameraAt(keys: CameraKey[], t: number): CameraKey {
  if (!keys.length) return {at:0,position:[0,1,16],lookAt:[0,.4,0],fov:38};
  if (t <= keys[0].at) return keys[0];
  for (let i=1;i<keys.length;i++) {
    const k=keys[i], p=keys[i-1];
    if (t <= k.at) {
      const u=smooth((t-p.at)/Math.max(.001,k.at-p.at));
      const v=(a: V3,b:V3):V3 => [mix(a[0],b[0],u),mix(a[1],b[1],u),mix(a[2],b[2],u)];
      return {at:t,position:v(p.position,k.position),lookAt:v(p.lookAt,k.lookAt),fov:mix(p.fov,k.fov,u)};
    }
  }
  return keys[keys.length-1];
}

function Timber({x,y,z=0,w,h,color='#7f5645'}:{x:number;y:number;z?:number;w:number;h:number;color?:string}) {
  return <mesh position={[x,y,z]} castShadow receiveShadow><boxGeometry args={[w,h,.26]}/><meshStandardMaterial color={color} roughness={.93}/></mesh>;
}

function Wheel({x,t}:{x:number;t:number}) {
  return <group position={[x,-1.88,.68]} rotation={[0,0,-t*2.5]}>
    <mesh castShadow><torusGeometry args={[.46,.09,10,28]}/><meshStandardMaterial color="#161c27" metalness={.5} roughness={.55}/></mesh>
    {Array.from({length:6},(_,i)=><mesh key={i} rotation={[0,0,i*Math.PI/3]}>
      <boxGeometry args={[.06,.84,.06]}/><meshStandardMaterial color="#42434b" metalness={.4}/>
    </mesh>)}
    <mesh><sphereGeometry args={[.12,12,10]}/><meshStandardMaterial color="#9b795e"/></mesh>
  </group>;
}

function Train({t}:{t:number}) {
  const planks = useMemo(()=>Array.from({length:9},(_,i)=>i),[]);
  return <group>
    <Timber x={0} y={.1} z={-.4} w={7.8} h={3.6} color="#2c2b32"/>
    <Timber x={-2.7} y={.1} z={.2} w={2.4} h={3.6} color="#83513e"/>
    <Timber x={2.7} y={.1} z={.2} w={2.4} h={3.6} color="#83513e"/>
    <Timber x={0} y={1.71} z={.33} w={2.32} h={.35} color="#9b6b4d"/>
    <Timber x={0} y={-1.50} z={.36} w={2.45} h={.33} color="#694430"/>
    <Timber x={-1.46} y={.1} z={.4} w={.20} h={3.32} color="#ad7d58"/>
    <Timber x={1.46} y={.1} z={.4} w={.20} h={3.32} color="#ad7d58"/>
    {planks.map(i=><React.Fragment key={i}>
      <Timber x={-2.70} y={-1.36+i*.38} z={.38} w={2.3} h={.025} color="#422c29"/>
      <Timber x={2.70} y={-1.36+i*.38} z={.38} w={2.3} h={.025} color="#422c29"/>
    </React.Fragment>)}
    {[-3.9,3.9].map((x,i)=><Timber key={i} x={x} y={.1} z={.5} w={.12} h={3.8} color="#44312d"/>)}
    <mesh position={[0,-1.66,0]}><boxGeometry args={[8,.27,2.5]}/><meshStandardMaterial color="#4e3a2f"/></mesh>
    <mesh position={[0,2.06,0]}><boxGeometry args={[8,.25,2.6]}/><meshStandardMaterial color="#43312e"/></mesh>
    {[-2.85,-1.85,1.85,2.85].map((x,i)=><Wheel key={i} x={x} t={t}/>)}
    <pointLight position={[-.5,1.48,1.15]} intensity={2.0+.10*Math.sin(t*6.1)} distance={6} color="#ffc481"/>
    <mesh position={[-.5,1.45,1.0]}><sphereGeometry args={[.11,12,10]}/><meshBasicMaterial color="#ffd28c"/></mesh>
  </group>;
}

function Ground({t,day}:{t:number;day:boolean}) {
  const trees = useMemo(()=>Array.from({length:54},(_,i)=>({x:((i*3.87)%34)-17,z:-5-i%4*3.1,
    s:.8+(i*11%8)*.16})),[]);
  return <group>
    <mesh position={[0,-3.0,-7]} rotation={[-Math.PI/2,0,0]} receiveShadow>
      <planeGeometry args={[120,130]}/><meshStandardMaterial color={day?'#596c4f':'#1a3544'} roughness={1}/>
    </mesh>
    {/* the train is stationary relative to camera; landscape flows past at different depths */}
    {trees.map((tree,i)=>{
      const x=((tree.x-t*(1.2+(i%3)*.25)+25)%50)-25;
      return <group key={i} position={[x,-1.0,tree.z]} scale={[tree.s,tree.s,tree.s]}>
        <mesh position={[0,1.5,0]} castShadow><coneGeometry args={[.73,3.2,7]}/>
          <meshStandardMaterial color={day?'#36583d':'#193a43'} flatShading roughness={1}/></mesh>
        <mesh position={[0,0,0]}><cylinderGeometry args={[.09,.15,1,7]}/><meshStandardMaterial color="#3c3034"/></mesh>
      </group>;
    })}
    {Array.from({length:14},(_,i)=><mesh key={i} position={[(i-7)*1.6,-2.54,.15]}>
      <boxGeometry args={[.30,.18,1.65]}/><meshStandardMaterial color="#5a514e"/>
    </mesh>)}
    {[-.75,.75].map((z,i)=><mesh key={i} position={[0,-2.42,z]}><boxGeometry args={[70,.08,.12]}/>
      <meshStandardMaterial color="#858b8e" metalness={.75} roughness={.45}/></mesh>)}
  </group>;
}

export const World:React.FC<{story:Story}>=({story})=>{
  const frame=useCurrentFrame(), t=frame/story.fps;
  const {camera,scene}=useThree();
  const day=story.environment==='town_day';
  const k=cameraAt(story.camera,t);
  camera.position.set(k.position[0],k.position[1],k.position[2]);
  camera.lookAt(k.lookAt[0],k.lookAt[1],k.lookAt[2]);
  if ('fov' in camera && typeof camera.fov==='number') {camera.fov=k.fov;camera.updateProjectionMatrix();}
  scene.fog=null;
  return <>
    <color attach="background" args={[day?'#86b7da':'#0d2137']}/>
    <ambientLight intensity={day?1.7:.9}/>
    <hemisphereLight args={[day?'#d3e8ff':'#7297d2','#212236',day?1.3:1.0]}/>
    <directionalLight position={[-8,12,6]} intensity={day?2.1:1.1} color={day?'#ffe4b8':'#9eb9ff'} castShadow shadow-mapSize={[1024,1024]}/>
    <Ground t={t} day={day}/>
    <Train t={t}/>
    {story.actors.slice(0,4).map((actor,i)=><ActorRig key={actor.id} actor={actor} index={i} events={story.events} t={t}/>)}
  </>;
};
