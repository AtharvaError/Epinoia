import { useEffect, useRef, useState } from "react";

const SCAN_EVERY_MS = 150;
const SCAN_WIDTH = 640;

export interface QrScannerProps {
  onCode: (code: string) => void;
  label?: string;
  errorMessage?: string;
}

/**
 * Reusable QR scanner component that reads QR codes from the device camera.
 * Dynamically imports `jsqr` so pages only pay for the dependency when opened.
 */
export function QrScanner({
  onCode,
  label = "Camera view for scanning QR code",
  errorMessage,
}: QrScannerProps) {
  const video = useRef<HTMLVideoElement>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let stream: MediaStream | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;

    const start = async () => {
      if (!navigator.mediaDevices) {
        setProblem(
          errorMessage ||
            "Camera access needs a secure connection (HTTPS). Type the code or ID manually instead."
        );
        return;
      }
      try {
        const [{ default: jsQR }, media] = await Promise.all([
          import("jsqr"),
          navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } }),
        ]);
        stream = media;
        if (stopped || !video.current) return;
        video.current.srcObject = media;
        await video.current.play();
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d", { willReadFrequently: true })!;

        const scan = () => {
          const frame = video.current;
          if (stopped || !frame) return;
          if (frame.videoWidth) {
            canvas.width = Math.min(SCAN_WIDTH, frame.videoWidth);
            canvas.height = Math.round((frame.videoHeight / frame.videoWidth) * canvas.width);
            context.drawImage(frame, 0, 0, canvas.width, canvas.height);
            const found = jsQR(
              context.getImageData(0, 0, canvas.width, canvas.height).data,
              canvas.width,
              canvas.height
            );
            if (found?.data) {
              onCode(found.data);
              return;
            }
          }
          timer = setTimeout(scan, SCAN_EVERY_MS);
        };
        scan();
      } catch {
        if (!stopped) {
          setProblem(
            errorMessage ||
              "The camera could not start. Allow camera access, or type the code manually."
          );
        }
      }
    };

    start();
    return () => {
      stopped = true;
      clearTimeout(timer);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [onCode, errorMessage]);

  if (problem) {
    return (
      <p className="form-error" role="alert">
        {problem}
      </p>
    );
  }

  return (
    <div className="scanner">
      <video ref={video} muted playsInline aria-label={label} />
      <span className="scan-frame" aria-hidden="true" />
    </div>
  );
}
