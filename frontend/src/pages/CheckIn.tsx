import { Camera as CameraIcon, CircleCheck, CircleX, ScanLine, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ErrorNote, Field, Moment, useAction, useFest } from "../components/ui";
import { api } from "../lib/api";

interface Pass {
  valid: boolean; code?: string; full_name?: string; college?: string; checkin_status?: string; registration_status?: string; tshirt_size?: string | null;
  merch?: { item: string; issued_at: string; issued_by: string | null }[]; merch_items?: string[];
}

const SCAN_EVERY_MS = 150;
const SCAN_WIDTH = 640;

/** Reads QR codes from the rear camera. jsQR loads here so no other page pays for it. */
function Camera({ onCode }: { onCode: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    let stream: MediaStream | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const start = async () => {
      if (!navigator.mediaDevices) {
        setProblem("Camera access needs a secure connection (HTTPS). Type the pass code below instead.");
        return;
      }
      try {
        const [{ default: jsQR }, media] = await Promise.all([import("jsqr"), navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })]);
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
            const found = jsQR(context.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
            if (found?.data) {
              onCode(found.data);
              return;
            }
          }
          timer = setTimeout(scan, SCAN_EVERY_MS);
        };
        scan();
      } catch {
        if (!stopped) setProblem("The camera could not start. Allow camera access, or type the pass code below.");
      }
    };
    start();
    return () => {
      stopped = true;
      clearTimeout(timer);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [onCode]);
  if (problem) return <p className="form-error" role="alert">{problem}</p>;
  return <div className="scanner"><video ref={video} muted playsInline aria-label="Camera view for scanning a pass" /><span className="scan-frame" aria-hidden="true" /></div>;
}

export function CheckIn() {
  const { base } = useFest();
  const [token, setToken] = useState("");
  const [scanning, setScanning] = useState(false);
  const [done, setDone] = useState<string>();
  const [count, setCount] = useState(0);
  const verify = useAction((code: string) => api<Pass>(`${base}/passes/verify`, { method: "POST", body: JSON.stringify({ token: code }) }));
  const checkIn = useAction(() => api<{ full_name: string }>(`${base}/passes/check-in`, { method: "POST", body: JSON.stringify({ token }) }), {
    onDone: (r) => {
      setDone(r.full_name);
      setCount((n) => n + 1);
      verify.mutate(token);
    },
  });
  const issue = useAction((item: string) => api<{ item: string; size: string | null }>(`${base}/passes/merch`, { method: "POST", body: JSON.stringify({ token, item }) }), {
    success: (r) => `${r.item}${r.size ? ` ${r.size}` : ""} issued`, onDone: () => verify.mutate(token),
  });
  const pass = verify.data;
  const eligible = pass?.valid && pass.registration_status !== "cancelled";

  const issueReset = useRef(issue.reset);
  issueReset.current = issue.reset;
  const read = useRef((code: string) => {
    setScanning(false);
    setToken(code);
    setDone(undefined);
    issueReset.current();
    verify.mutate(code);
  });
  const next = () => {
    setToken("");
    setDone(undefined);
    verify.reset();
    checkIn.reset();
    issue.reset();
    setScanning(true);
  };

  return (
    <div className="check-in">
      <Moment eyebrow={count > 0 ? `${count} checked in on this device` : "Check-in"} title="Scan a pass" />

      {done && (
        <div className="scan-result ok" role="status">
          <CircleCheck size={44} aria-hidden="true" />
          <p className="scan-title">{done}</p>
          <p>Checked in</p>
        </div>
      )}
      {pass && !pass.valid && (
        <div className="scan-result bad" role="alert">
          <CircleX size={44} aria-hidden="true" />
          <p className="scan-title">Not a valid pass</p>
          <p>Send this person to the help desk.</p>
        </div>
      )}
      {pass?.valid && pass.registration_status === "cancelled" && (
        <div className="scan-result bad" role="alert">
          <CircleX size={44} aria-hidden="true" />
          <p className="scan-title">{pass.full_name}</p>
          <p>This registration was cancelled. {pass.code}</p>
        </div>
      )}
      {!done && pass?.valid && pass.registration_status !== "cancelled" && pass.checkin_status === "checked_in" && (
        <div className="scan-result warn" role="alert">
          <TriangleAlert size={44} aria-hidden="true" />
          <p className="scan-title">{pass.full_name}</p>
          <p>Already checked in. {pass.code}</p>
        </div>
      )}
      {pass?.valid && pass.registration_status !== "cancelled" && pass.checkin_status !== "checked_in" && (
        <div className="scan-result ready">
          <p className="scan-title">{pass.full_name}</p>
          <p>{pass.college} · {pass.code}</p>
          <button className="button-primary huge" disabled={checkIn.isPending} onClick={() => checkIn.mutate()}>{checkIn.isPending ? "Checking in" : "Check in"}</button>
        </div>
      )}
      {eligible && (
        <section className="merch-desk" aria-label="Merchandise">
          <h2>Merchandise</h2>
          <div className="merch-items">
            {pass.merch_items?.map((item) => {
              const given = pass.merch?.find((m) => m.item === item);
              return <button key={item} className={given ? "merch-item given" : "merch-item"} disabled={issue.isPending} onClick={() => issue.mutate(item)}>{given ? <CircleCheck size={16} aria-hidden="true" /> : null}{given ? `${item} collected` : `Issue ${item}${item === "T-shirt" && pass.tshirt_size ? ` (${pass.tshirt_size})` : ""}`}</button>;
            })}
          </div>
          {issue.error && <div className="scan-result bad" role="alert"><CircleX size={32} aria-hidden="true" /><p>{issue.error.message}</p></div>}
        </section>
      )}
      <ErrorNote error={verify.error || checkIn.error} />

      {scanning ? <Camera onCode={read.current} /> : (
        <button className={pass?.valid && pass.checkin_status !== "checked_in" && pass.registration_status !== "cancelled" ? "button-secondary huge" : "button-primary huge"} onClick={next}>
          {done || pass ? <ScanLine size={22} /> : <CameraIcon size={22} />} {done || pass ? "Scan next" : "Start camera"}
        </button>
      )}
      {scanning && <button className="button-secondary huge" onClick={() => setScanning(false)}>Stop camera</button>}

      <form className="form" onSubmit={(e) => { e.preventDefault(); read.current(token.trim()); }}>
        <Field label="Or type the pass code" hint="A USB or Bluetooth QR reader types it here too.">
          <input required minLength={8} autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <button className="button-secondary" disabled={verify.isPending}>Verify pass</button>
      </form>
    </div>
  );
}
