// Reading a tag off a QR or barcode with the phone's camera.
//
// Two decoders, because the phones on a farm are whatever they are:
//
// - BarcodeDetector, built into Chrome on Android, reads QR codes and the
//   common one-dimensional barcodes (Code 128, Code 39, EAN) straight off
//   the video, fast and with no library;
// - jsQR (shared/vendor/jsqr, Apache 2.0), a pure-JavaScript QR reader,
//   for Safari on an iPhone, which has no BarcodeDetector. QR only: the
//   one-dimensional readers in JavaScript are either large or unreliable
//   in sun, and a farm that prints its own tags prints QR.
//
// The camera needs a secure page. Over Tailscale that is the https
// address every device already uses; on a plain http address the browser
// hides the camera and the scanner says so rather than sitting on black.
//
// The scanner is a full-screen overlay opened by the caller through its
// modal mechanism (so the back button closes it) and stopped through
// KuddeScan.stop(), which releases the camera - leaving a camera running
// behind a closed sheet drains the battery and keeps the light on.
const KuddeScan = {
  _stream: null,
  _raf: null,
  _onResult: null,

  supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  },

  // Starts the camera into `video` and calls onResult(text) once with the
  // first code read. Resolves when the camera is running, rejects with a
  // message for the user when it cannot be.
  async start(video, onResult) {
    KuddeScan.stop();
    if (!KuddeScan.supported()) {
      throw new Error(window.isSecureContext
        ? "This phone's browser has no camera access"
        : "The camera only works on the secure https address");
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
    } catch (e) {
      const name = e && e.name;
      throw new Error(name === "NotAllowedError" ? "Camera access was refused - allow it in the browser's settings"
        : name === "NotFoundError" ? "No camera on this device"
        : "Could not start the camera");
    }
    KuddeScan._stream = stream;
    KuddeScan._onResult = onResult;
    video.srcObject = stream;
    video.setAttribute("playsinline", "true");   // iOS: inline, not full-screen player
    await video.play();

    const detector = ("BarcodeDetector" in window)
      ? new BarcodeDetector({ formats: ["qr_code", "code_128", "code_39", "ean_13", "ean_8"] })
      : null;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let busy = false;

    const tick = async () => {
      if (!KuddeScan._stream) return;
      if (!busy && video.readyState >= 2) {
        busy = true;
        try {
          let text = null;
          if (detector) {
            const codes = await detector.detect(video);
            if (codes.length) text = codes[0].rawValue;
          } else if (typeof jsQR === "function") {
            // Decode at a reduced size: jsQR on a full 720p frame takes
            // long enough on an older iPhone to make the preview stutter.
            const scale = Math.min(1, 640 / video.videoWidth);
            canvas.width = Math.round(video.videoWidth * scale);
            canvas.height = Math.round(video.videoHeight * scale);
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const code = jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
            if (code && code.data) text = code.data;
          }
          if (text) {
            const cb = KuddeScan._onResult;
            KuddeScan.stop();
            if (cb) cb(String(text).trim());
            return;
          }
        } catch (_) { /* a frame that would not decode: try the next */ }
        busy = false;
      }
      KuddeScan._raf = requestAnimationFrame(tick);
    };
    KuddeScan._raf = requestAnimationFrame(tick);
  },

  stop() {
    if (KuddeScan._raf) cancelAnimationFrame(KuddeScan._raf);
    KuddeScan._raf = null;
    if (KuddeScan._stream) {
      for (const track of KuddeScan._stream.getTracks()) track.stop();
    }
    KuddeScan._stream = null;
    KuddeScan._onResult = null;
  },
};
