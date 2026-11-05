let stopCurrent: (() => void) | null = null;

/** Only authenticated backend notice audio from native xAI TTS. */
export function playNotice(url: string, timeoutMs = 20000): Promise<void> {
  if (!/^\/api\/rover\/notices\/[a-f0-9]{32}\/audio$/.test(url)) return Promise.reject(new Error('Invalid voice source.'));
  return new Promise((resolve, reject) => {
    const audio = new Audio(url);
    let done = false;
    const finish = (error?: Error) => {
      if (done) return;
      done = true; clearTimeout(timer); audio.pause(); audio.removeAttribute('src');
      if (stopCurrent === stop) stopCurrent = null;
      error ? reject(error) : resolve();
    };
    const stop = () => finish();
    stopCurrent = stop;
    const timer = setTimeout(() => finish(new Error('Voice playback timed out.')), timeoutMs);
    audio.onended = () => finish();
    audio.onerror = () => finish(new Error('Grok voice is unavailable.'));
    void audio.play().catch(() => finish(new Error('Tap Enable voice to allow playback.')));
  });
}
export function stopSpeaking(): void { stopCurrent?.(); }
