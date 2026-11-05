import { Command } from 'lucide-react';
import CameraWidget from './CameraWidget';
import RoverChat from './RoverChat';
import { CAMERA_FEED, CAMERA_STATUS, ROVER_CAMERA_FEED, ROVER_CAMERA_STATUS } from './camera';
import './operator.css';

const waiting = 'Waiting for the backend to link this camera.';

async function refreshOperatorSession() {
  await fetch('/__local/session', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-Rovr-Local': '1' },
    cache: 'no-store',
  });
}

export default function App() {
  return <div className="operator">
    <header className="operator-bar">
      <a className="brand" href="#fixed-camera" aria-label="ROVR home"><div className="brand-mark"><Command size={22}/></div><span>rovr<span className="brand-dot">.</span></span></a>
      <div>
        <div className="eyebrow">OPERATOR</div>
        <strong>Cameras and rover updates</strong>
        <p>Fixed camera, rover camera, and the rover’s spoken progress.</p>
      </div>
    </header>
    <main className="operator-stage">
      <section className="operator-camera" id="fixed-camera">
        <CameraWidget enabled quiet title="Fixed camera" statusUrl={CAMERA_STATUS} feedUrl={CAMERA_FEED} waitingMessage={waiting} onRefreshSession={refreshOperatorSession}/>
      </section>
      <section className="operator-camera" id="rover-camera">
        <CameraWidget enabled quiet title="Rover camera" statusUrl={ROVER_CAMERA_STATUS} feedUrl={ROVER_CAMERA_FEED} waitingMessage={waiting} onRefreshSession={refreshOperatorSession}/>
      </section>
      <RoverChat/>
    </main>
  </div>;
}
