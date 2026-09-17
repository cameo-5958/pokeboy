export function blobToDataUri(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("Could not encode cartridge label"));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Could not read cartridge label"));
    reader.onabort = () => reject(new Error("Cartridge label read was aborted"));
    reader.readAsDataURL(blob);
  });
}

/** Short opaque id - good enough to distinguish devices/sessions, not a real UUID. */
export function genId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
