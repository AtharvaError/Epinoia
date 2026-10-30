// CSV parsers — one per source file in /data/raw.
// Each parser: read -> Zod-validate row -> normalize labels -> return {rows, rejects}.
// Rejects are kept (not dropped) with the reason they failed validation.
//
// TODO: parseParticipants, parseRegistrations, parseContingents, parseEvents,
//       parseVenues, parseHostels, parseExternalStays, parseInventoryLogistics,
//       parseInventoryMerch, parseVendors, parseCoreTeam.
export {};
