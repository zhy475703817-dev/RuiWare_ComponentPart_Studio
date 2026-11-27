// Leave browser connections available for saving and synchronizing the draft.
const MAX_ACTIVE = 2;
let active = 0;
const waiting: Array<() => void> = [];

function acquire(signal: AbortSignal): Promise<() => void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => {
      const index = waiting.indexOf(start);
      if (index >= 0) waiting.splice(index, 1);
      reject(signal.reason);
    };
    const start = () => {
      signal.removeEventListener("abort", abort);
      active++;
      resolve(() => {
        active--;
        waiting.shift()?.();
      });
    };
    if (active < MAX_ACTIVE) start();
    else {
      waiting.push(start);
      signal.addEventListener("abort", abort, { once: true });
    }
  });
}

export async function withReconstructionSlot<T>(signal: AbortSignal, operation: () => Promise<T>): Promise<T> {
  const release = await acquire(signal);
  try {
    signal.throwIfAborted();
    return await operation();
  } finally { release(); }
}
