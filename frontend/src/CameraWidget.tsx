import {Video} from 'lucide-react';

export default function CameraWidget() {
  return <section className="panel main-camera-widget" aria-labelledby="main-camera-title">
    <div className="panel-heading"><div className="row"><Video size={16}/><h2 id="main-camera-title">Main camera</h2></div><span className="badge neutral">Not connected</span></div>
    <div className="main-camera-empty" role="img" aria-label="Main camera feed, empty"/>
  </section>;
}
