export function canSpeak(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined';
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices();
  return voices.find(voice => /en[-_](US|GB)/i.test(voice.lang)) ?? voices.find(voice => voice.lang.toLowerCase().startsWith('en')) ?? null;
}

/** Browser speech until the backend provides Grok audio. Resolves on end, error, or timeout. */
export function speak(text: string, timeoutMs = 12000): Promise<void> {
  return new Promise(resolve => {
    if (!canSpeak()) {
      resolve();
      return;
    }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      resolve();
    };
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-US';
    utterance.rate = 1;
    const voice = pickVoice();
    if (voice) utterance.voice = voice;
    utterance.onend = finish;
    utterance.onerror = finish;
    const timer = window.setTimeout(() => {
      window.speechSynthesis.cancel();
      finish();
    }, timeoutMs);
    window.speechSynthesis.cancel();
    window.speechSynthesis.resume();
    window.speechSynthesis.speak(utterance);
  });
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}
