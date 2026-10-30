// Data-quality flagging — attach QualityFlag[] to records at import time.
// Preserve uncertainty: a flagged record stays usable but is marked unverified
// and requires human resolution before it influences a HARD decision.
// TODO: rules producing duplicate_suspected / missing_gender / timing_anomaly /
//       cross_file_conflict / unverified.
export {};
