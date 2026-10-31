import { Camera as CameraIcon, CircleCheck, CircleX, ScanLine, TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";

import { QrScanner } from "../components/QrScanner";
import { ErrorNote, Field, Moment, useAction, useFest } from "../components/ui";
import { api } from "../lib/api";

interface Pass {
  valid: boolean;
  code?: string;
  full_name?: string;
  college?: string;
  checkin_status?: string;
  registration_status?: string;
}

export function CheckIn() {
  const { base } = useFest();
  const [token, setToken] = useState("");
  const [scanning, setScanning] = useState(false);
  const [done, setDone] = useState<string>();
  const [count, setCount] = useState(0);
  const verify = useAction((code: string) =>
    api<Pass>(`${base}/passes/verify`, { method: "POST", body: JSON.stringify({ token: code }) })
  );
  const checkIn = useAction(
    () => api<{ full_name: string }>(`${base}/passes/check-in`, { method: "POST", body: JSON.stringify({ token }) }),
    {
      onDone: (r) => {
        setDone(r.full_name);
        setCount((n) => n + 1);
        verify.reset();
      },
    }
  );
  const pass = verify.data;

  const read = useRef((code: string) => {
    setScanning(false);
    setToken(code);
    setDone(undefined);
    verify.mutate(code);
  });
  const next = () => {
    setToken("");
    setDone(undefined);
    verify.reset();
    checkIn.reset();
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
      {pass?.valid && pass.registration_status !== "cancelled" && pass.checkin_status === "checked_in" && (
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
      <ErrorNote error={verify.error || checkIn.error} />

      {scanning ? (
        <QrScanner onCode={read.current} label="Camera view for scanning a pass" />
      ) : (
        <button
          className={pass?.valid && pass.checkin_status !== "checked_in" && pass.registration_status !== "cancelled" ? "button-secondary huge" : "button-primary huge"}
          onClick={next}
        >
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
