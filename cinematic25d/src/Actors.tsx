import React from 'react';
import type { Actor, Event } from './types';

const clamp = (x: number) => Math.max(0, Math.min(1, x));
const ease = (x: number) => { const v = clamp(x); return v * v * (3 - 2 * v); };

function strength(events: Event[], actor: string, action: Event['action'], t: number): number {
  let result = 0;
  for (const event of events) {
    if (event.actor !== actor || event.action !== action) continue;
    const u = (t - event.at) / Math.max(.01, event.duration);
    if (u < 0 || u > 1) continue;
    result = Math.max(result, ease(u * 5) * ease((1 - u) * 5));
  }
  return result;
}

const oval = (w: number, h: number) => <sphereGeometry args={[1, 18, 12]} />;

/** Bone pivots live at shoulders/hips; child meshes extend down from joint. No single-image zoom. */
function Limb({ color, length, width = .15, angle = 0, hand = false }: {
  color: string; length: number; width?: number; angle?: number; hand?: boolean;
}) {
  return <group rotation={[0, 0, angle]}>
    <mesh position={[0, -length / 2, 0]} scale={[width, length / 2, .07]}>
      <sphereGeometry args={[1, 12, 10]}/>
      <meshStandardMaterial color={color} roughness={.88}/>
    </mesh>
    {hand && <mesh position={[0, -length - .045, .03]} scale={[width * .72, .10, .055]}>
      <sphereGeometry args={[1, 12, 8]}/><meshStandardMaterial color="#efc5a5"/>
    </mesh>}
  </group>;
}

export const ActorRig: React.FC<{actor: Actor; index: number; events: Event[]; t: number}> = ({actor, index, events, t}) => {
  const fear = strength(events, actor.id, 'fear', t);
  const look = strength(events, actor.id, 'look', t);
  const lantern = strength(events, actor.id, 'lantern', t);
  const reach = strength(events, actor.id, 'reach', t);
  const walk = Math.max(strength(events, actor.id, 'walk', t), strength(events, actor.id, 'run', t));
  const talk = strength(events, actor.id, 'talk', t);
  const phase = t * 8 + index * 2;
  const stepping = walk * Math.sin(phase);
  const blink = Math.sin(t * 2.4 + index * 3.2) > .985 ? .1 : 1;
  const swing = Math.sin(t * 1.9 + index) * .025;
  const faceAngle = -look * .2 + fear * .11;
  const front = index % 2 === 0 ? -.78 : .78;
  const c = actor.palette;
  return <group position={[front, -.88 + .025 * Math.sin(phase), .83]} scale={[1.0, 1.0, 1.0]}>
    <mesh position={[0, -.02, -.14]} rotation={[-Math.PI / 2, 0, 0]} scale={[.47, .21, 1]}>
      <circleGeometry args={[1, 24]}/><meshBasicMaterial color="#071018" transparent opacity={.38} depthWrite={false}/>
    </mesh>
    <group position={[0, .90 + swing, 0]}>
      {/* back arm */}
      <group position={[.36, .10, -.02]}><Limb color={c.skin} length={.70} angle={-.14 - reach*.5 - lantern*1.6 + stepping*.4} hand /></group>
      <group position={[0, 0, .08]}>
        <mesh scale={[.46, .58, .12]}><sphereGeometry args={[1, 24, 16]}/>
          <meshStandardMaterial color={c.clothes} roughness={.95}/></mesh>
        <mesh scale={[.50, .61, .07]} position={[0,-.01,-.085]}><sphereGeometry args={[1, 22, 12]}/>
          <meshStandardMaterial color="#27212a" roughness={1}/></mesh>
      </group>
      {/* front arm: lifts lantern in screen space */}
      <group position={[-.37, .13, .18]} rotation={[0,0,.10 + lantern*1.3 + stepping*.35]}>
        <Limb color={c.skin} length={.67} angle={0} hand />
        <group position={[0,-.81,.13]}>
          <mesh scale={[.14,.19,.1]}><boxGeometry args={[1,1,1]}/><meshStandardMaterial color="#33343b" metalness={.45}/></mesh>
          <mesh position={[0,.04,.08]} scale={[.08,.10,.03]}><circleGeometry args={[1,16]}/>
            <meshBasicMaterial color="#ffe3a6"/></mesh>
          <pointLight color="#ffd494" intensity={1.2 * lantern} distance={4}/>
        </group>
      </group>
      {/* two independent legs */}
      <group position={[-.18,-.45,-.005]}><Limb color="#3a3e54" length={.65} width={.17} angle={stepping*.28}/></group>
      <group position={[.18,-.45,-.005]}><Limb color="#3a3e54" length={.65} width={.17} angle={-stepping*.28}/></group>
      <group position={[0,.81 + fear*.07, .20]} rotation={[0, look*.18, faceAngle]}>
        {/* head with thick outline, warm skin, stylized hair */}
        <mesh scale={[.46,.49,.13]}><sphereGeometry args={[1,28,16]}/><meshBasicMaterial color="#26202a"/></mesh>
        <mesh scale={[.43,.46,.14]} position={[0,.01,.025]}><sphereGeometry args={[1,28,16]}/><meshStandardMaterial color={c.skin} roughness={.96}/></mesh>
        <mesh position={[0,.31,.10]} scale={[.46,.20,.16]}><sphereGeometry args={[1,28,12]}/>
          <meshStandardMaterial color={c.hair} roughness={1}/></mesh>
        <mesh position={[-.155,.055,.158]} scale={[.067,.084 * blink*(1+fear*.45),.02]}>
          <sphereGeometry args={[1,12,8]}/><meshBasicMaterial color="#20252c"/></mesh>
        <mesh position={[.155,.055,.158]} scale={[.067,.084 * blink*(1+fear*.45),.02]}>
          <sphereGeometry args={[1,12,8]}/><meshBasicMaterial color="#20252c"/></mesh>
        <mesh position={[0,-.17,.16]} scale={[.07, .019+fear*.055+talk*.045,.02]}>
          <sphereGeometry args={[1,12,8]}/><meshBasicMaterial color="#4d2532"/></mesh>
      </group>
    </group>
  </group>;
};
