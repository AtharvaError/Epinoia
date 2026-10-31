import { useQuery, useQueryClient } from "@tanstack/react-query";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Compass,
  Copy,
  ExternalLink,
  MapPin,
  Pencil,
  Phone,
  PhoneCall,
  Plus,
  RefreshCw,
  Search,
  Send,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  DataTable,
  Dialog,
  ErrorNote,
  Field,
  Pagination,
  Pill,
  QueryState,
  notify,
  useAction,
  useDebounced,
  useFest,
} from "./ui";
import {
  api,
  type ExternalHotelItem,
  type StaySuggestionHistoryItem,
} from "../lib/api";
import { formatTime } from "../lib/format";

interface StaySuggestionsResponse {
  campus: {
    name: string;
    latitude: number;
    longitude: number;
  };
  radius_km: number;
  total: number;
  items: ExternalHotelItem[];
}

interface ParticipantSearchResult {
  id: number;
  code: string;
  name: string;
  college: string;
  phone: string | null;
  arrival_at: string | null;
  telegram_status: "linked" | "not_started";
  telegram_chat_id: string | null;
  telegram_username: string | null;
}

export function StaySuggestionsDrawer({ onClose }: { onClose: () => void }) {
  const { base, can } = useFest();
  const queryClient = useQueryClient();

  // Search & filter state
  const [tab, setTab] = useState<"explore" | "history">("explore");
  const [radius, setRadius] = useState<number>(10);
  const [search, setSearch] = useState("");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [hasPhone, setHasPhone] = useState(false);
  const [minRating, setMinRating] = useState<string>("");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [highlightedId, setHighlightedId] = useState<number | null>(null);

  // Responsive mobile tab
  const [mobileTab, setMobileTab] = useState<"list" | "map">("list");

  // Dialogs
  const [dialog, setDialog] = useState<"send" | "add_hotel" | "edit_hotel" | "edit_campus" | null>(null);
  const [hotelToEdit, setHotelToEdit] = useState<ExternalHotelItem | null>(null);

  const q = useDebounced(search, 250);

  // Query hotels
  const queryParams = new URLSearchParams();
  queryParams.set("radius", radius.toString());
  if (q.trim()) queryParams.set("q", q.trim());
  if (verifiedOnly) queryParams.set("verified_only", "true");
  if (hasPhone) queryParams.set("has_phone", "true");
  if (minRating) queryParams.set("min_rating", minRating);

  const hotelsQuery = useQuery({
    queryKey: ["external-hotels", base, radius, q, verifiedOnly, hasPhone, minRating],
    queryFn: () => api<StaySuggestionsResponse>(`${base}/external-hotels?${queryParams.toString()}`),
  });

  const hotels = hotelsQuery.data?.items ?? [];
  const campus = hotelsQuery.data?.campus ?? { name: "IIM Indore", latitude: 22.5838, longitude: 75.7656 };

  // Refresh remote hotels
  const refresh = useAction(
    () => api<{ status: string; added: number }>(`${base}/external-hotels/refresh?radius=${radius}`, { method: "POST" }),
    {
      success: (r) => `Refreshed hotels (${r.added} added)`,
      onDone: () => queryClient.invalidateQueries({ queryKey: ["external-hotels"] }),
    }
  );

  // Delete hotel
  const removeHotel = useAction(
    (id: number) => api(`${base}/external-hotels/${id}`, { method: "DELETE" }),
    {
      success: "Hotel removed",
      onDone: () => {
        setSelectedIds((prev) => prev.filter((i) => i !== hotelToEdit?.id));
        queryClient.invalidateQueries({ queryKey: ["external-hotels"] });
      },
    }
  );

  // Toggle verify hotel
  const toggleVerify = useAction(
    ({ id, is_verified }: { id: number; is_verified: boolean }) =>
      api<ExternalHotelItem>(`${base}/external-hotels/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_verified }),
      }),
    {
      success: (h: ExternalHotelItem) => (h.is_verified ? "Hotel marked as Verified" : "Hotel marked as Unverified"),
      onDone: () => queryClient.invalidateQueries({ queryKey: ["external-hotels"] }),
    }
  );

  // Toggle selection
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) {
        return prev.filter((i) => i !== id);
      }
      if (prev.length >= 5) {
        notify("You can select up to 5 hotels to send at once.");
        return prev;
      }
      return [...prev, id];
    });
  };

  const selectedHotels = useMemo(
    () => hotels.filter((h) => selectedIds.includes(h.id)),
    [hotels, selectedIds]
  );

  return (
    <div className="stay-drawer-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="stay-drawer-shell" role="dialog" aria-modal="true" aria-labelledby="stay-drawer-title">
        {/* Top Header Bar */}
        <header className="stay-drawer-head">
          <div className="head-left">
            <div className="title-group">
              <h2 id="stay-drawer-title">
                <Compass className="icon-accent" size={20} /> External Stay Suggestions
              </h2>
              <span className="badge-scope">Outside Accommodation Only • No Bed Budget</span>
            </div>
            <div className="drawer-tabs">
              <button
                className={`drawer-tab ${tab === "explore" ? "active" : ""}`}
                onClick={() => setTab("explore")}
              >
                Hotels & Map ({hotels.length})
              </button>
              <button
                className={`drawer-tab ${tab === "history" ? "active" : ""}`}
                onClick={() => setTab("history")}
              >
                Sent History
              </button>
            </div>
          </div>

          <div className="head-right">
            {tab === "explore" && (
              <>
                <button
                  className="button-ghost small"
                  title="Refresh or pull remote hotels"
                  disabled={refresh.isPending}
                  onClick={() => refresh.mutate()}
                >
                  <RefreshCw size={14} className={refresh.isPending ? "spin" : ""} /> Refresh
                </button>
                {can("accommodation.write") && (
                  <button
                    className="button-ghost small"
                    onClick={() => {
                      setHotelToEdit(null);
                      setDialog("add_hotel");
                    }}
                  >
                    <Plus size={14} /> Add hotel
                  </button>
                )}
                <button
                  className="button-ghost small"
                  title="Configure campus pin coordinates"
                  onClick={() => setDialog("edit_campus")}
                >
                  <MapPin size={14} /> Campus pin
                </button>
              </>
            )}
            <button className="icon-button" aria-label="Close" onClick={onClose}>
              <X size={20} />
            </button>
          </div>
        </header>

        {/* Tab 1: Explore & Map */}
        {tab === "explore" && (
          <>
            {/* Filter Bar */}
            <div className="stay-filter-bar">
              <div className="search-wrap">
                <Search size={15} className="search-icon" />
                <input
                  type="search"
                  aria-label="Search hotels"
                  placeholder="Search by hotel name, area, or address..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>

              <div className="radius-selector" role="group" aria-label="Distance Radius">
                <span className="filter-label">Radius:</span>
                {[5, 10, 15, 25].map((r) => (
                  <button
                    key={r}
                    type="button"
                    className={`radius-pill ${radius === r ? "active" : ""}`}
                    onClick={() => setRadius(r)}
                  >
                    {r} km
                  </button>
                ))}
              </div>

              <div className="filters-group">
                <label className="checkbox-chip">
                  <input
                    type="checkbox"
                    checked={verifiedOnly}
                    onChange={(e) => setVerifiedOnly(e.target.checked)}
                  />
                  <span>Verified only</span>
                </label>
                <label className="checkbox-chip">
                  <input
                    type="checkbox"
                    checked={hasPhone}
                    onChange={(e) => setHasPhone(e.target.checked)}
                  />
                  <span>Has phone</span>
                </label>
                <select
                  aria-label="Filter by rating"
                  value={minRating}
                  onChange={(e) => setMinRating(e.target.value)}
                  className="select-rating"
                >
                  <option value="">Any rating</option>
                  <option value="4.5">★ 4.5+ (Top rated)</option>
                  <option value="4.0">★ 4.0+ (Good)</option>
                  <option value="3.5">★ 3.5+ (Standard)</option>
                </select>
              </div>

              {/* Mobile View Toggle */}
              <div className="mobile-view-toggle">
                <button
                  type="button"
                  className={mobileTab === "list" ? "active" : ""}
                  onClick={() => setMobileTab("list")}
                >
                  List ({hotels.length})
                </button>
                <button
                  type="button"
                  className={mobileTab === "map" ? "active" : ""}
                  onClick={() => setMobileTab("map")}
                >
                  Map
                </button>
              </div>
            </div>

            {/* Split Content Area */}
            <div className="stay-main-split">
              {/* Left Panel: Hotel Cards */}
              <div className={`hotel-list-panel ${mobileTab === "map" ? "hide-on-mobile" : ""}`}>
                <div className="list-meta">
                  <span>
                    Found <strong>{hotels.length}</strong> {hotels.length === 1 ? "hotel" : "hotels"} within {radius} km
                    of {campus.name}
                  </span>
                  {selectedIds.length > 0 && (
                    <button
                      className="link small"
                      onClick={() => setSelectedIds([])}
                    >
                      Clear selected ({selectedIds.length})
                    </button>
                  )}
                </div>

                <QueryState query={hotelsQuery}>
                  {hotels.length === 0 ? (
                    <div className="empty-hotels-box">
                      <Compass size={36} className="muted" />
                      <h3>No hotels found in {radius} km</h3>
                      <p className="muted small">
                        Try expanding the radius to 15 or 25 km, or clear your filters to find more properties.
                      </p>
                      <div className="quick-radius-buttons">
                        {radius < 15 && (
                          <button className="button-secondary small" onClick={() => setRadius(15)}>
                            Expand to 15 km
                          </button>
                        )}
                        {radius < 25 && (
                          <button className="button-secondary small" onClick={() => setRadius(25)}>
                            Expand to 25 km
                          </button>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="hotel-cards-scroll">
                      {hotels.map((hotel) => {
                        const isSelected = selectedIds.includes(hotel.id);
                        const isHighlighted = highlightedId === hotel.id;

                        return (
                          <div
                            key={hotel.id}
                            id={`hotel-card-${hotel.id}`}
                            className={`hotel-card ${isSelected ? "selected" : ""} ${
                              isHighlighted ? "highlighted" : ""
                            }`}
                            onClick={() => setHighlightedId(hotel.id)}
                          >
                            <div className="card-top">
                              <label
                                className="hotel-select-check"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  onChange={() => toggleSelect(hotel.id)}
                                />
                                <span className="checkmark" />
                              </label>

                              <div className="hotel-header-info">
                                <div className="hotel-title-row">
                                  <h3 className="hotel-name">{hotel.name}</h3>
                                  {hotel.rating && (
                                    <span className="hotel-rating">
                                      <Star size={13} fill="currentColor" /> {hotel.rating.toFixed(1)}
                                    </span>
                                  )}
                                </div>
                                <div className="hotel-area-dist">
                                  <span className="dist-chip">
                                    <MapPin size={12} /> {hotel.distance_km} km
                                  </span>
                                  <span className="time-chip">
                                    <Clock size={12} /> ~{hotel.travel_time_min} mins by car
                                  </span>
                                  {hotel.area && <span className="area-text">• {hotel.area}</span>}
                                </div>
                              </div>
                            </div>

                            <div className="card-body">
                              <div className="price-phone-row">
                                <div className="price-pill">
                                  <span className="price-val">{hotel.price_display}</span>
                                </div>

                                <div className="phone-pill">
                                  {hotel.phone ? (
                                    <a
                                      href={`tel:${hotel.phone}`}
                                      className="phone-link"
                                      title="Tap to call hotel directly"
                                      onClick={(e) => e.stopPropagation()}
                                    >
                                      <PhoneCall size={13} /> {hotel.phone}
                                    </a>
                                  ) : (
                                    <span className="no-phone">
                                      <Phone size={13} /> Phone not available
                                    </span>
                                  )}
                                </div>
                              </div>

                              {hotel.address && (
                                <p className="hotel-address muted small">{hotel.address}</p>
                              )}

                              {hotel.notes && (
                                <p className="hotel-notes">{hotel.notes}</p>
                              )}

                              <div className="verification-row">
                                {hotel.is_verified ? (
                                  <div className="verified-status ok">
                                    <CheckCircle2 size={13} />
                                    <span>
                                      Verified{" "}
                                      {hotel.verified_at
                                        ? `on ${formatTime(hotel.verified_at)}`
                                        : ""}
                                      {hotel.verified_by ? ` by ${hotel.verified_by}` : ""}
                                    </span>
                                  </div>
                                ) : (
                                  <div className="verified-status warn">
                                    <AlertTriangle size={13} />
                                    <span>⚠️ Phone and price not confirmed</span>
                                  </div>
                                )}

                                <div className="card-actions" onClick={(e) => e.stopPropagation()}>
                                  <a
                                    href={hotel.google_maps_link}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="link-gmaps"
                                    title="Open Google Maps link"
                                  >
                                    <ExternalLink size={13} /> Map
                                  </a>

                                  {can("accommodation.write") && (
                                    <>
                                      <button
                                        type="button"
                                        className="btn-tiny"
                                        title={hotel.is_verified ? "Mark as unverified" : "Mark as verified"}
                                        onClick={() =>
                                          toggleVerify.mutate({
                                            id: hotel.id,
                                            is_verified: !hotel.is_verified,
                                          })
                                        }
                                      >
                                        {hotel.is_verified ? "Unverify" : "Verify"}
                                      </button>
                                      <button
                                        type="button"
                                        className="btn-tiny"
                                        title="Edit hotel details"
                                        onClick={() => {
                                          setHotelToEdit(hotel);
                                          setDialog("edit_hotel");
                                        }}
                                      >
                                        <Pencil size={12} />
                                      </button>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </QueryState>
              </div>

              {/* Right Panel: Leaflet Interactive Map */}
              <div className={`hotel-map-panel ${mobileTab === "list" ? "hide-on-mobile" : ""}`}>
                <StayLeafletMap
                  campus={campus}
                  radiusKm={radius}
                  hotels={hotels}
                  selectedIds={selectedIds}
                  highlightedId={highlightedId}
                  mobileTab={mobileTab}
                  onSelectHotel={toggleSelect}
                  onHighlightHotel={setHighlightedId}
                />
              </div>
            </div>

            {/* Bottom Sticky Action Bar */}
            <div className="stay-bottom-bar">
              <div className="bottom-bar-left">
                <span className="selected-counter">
                  <strong>{selectedIds.length}</strong> of 5 hotels selected
                </span>
                <div className="selected-chips">
                  {selectedHotels.map((h) => (
                    <span key={h.id} className="selected-chip">
                      {h.name}
                      <button
                        type="button"
                        onClick={() => toggleSelect(h.id)}
                        aria-label={`Deselect ${h.name}`}
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              </div>

              <div className="bottom-bar-right">
                <button
                  type="button"
                  className="button-primary"
                  disabled={selectedIds.length === 0}
                  onClick={() => setDialog("send")}
                >
                  <Send size={15} /> Send to participant ({selectedIds.length})
                </button>
              </div>
            </div>
          </>
        )}

        {/* Tab 2: Sent History */}
        {tab === "history" && <StaySentHistory base={base} />}

        {/* Modals */}
        {dialog === "send" && (
          <SendToParticipantModal
            base={base}
            selectedHotels={selectedHotels}
            campusName={campus.name}
            onClose={() => setDialog(null)}
            onSent={() => {
              setDialog(null);
              setSelectedIds([]);
              notify("Stay suggestions dispatched!");
            }}
          />
        )}

        {dialog === "add_hotel" && (
          <HotelFormModal
            base={base}
            campus={campus}
            onClose={() => setDialog(null)}
            onSaved={() => {
              setDialog(null);
              queryClient.invalidateQueries({ queryKey: ["external-hotels"] });
            }}
          />
        )}

        {dialog === "edit_hotel" && hotelToEdit && (
          <HotelFormModal
            base={base}
            campus={campus}
            existing={hotelToEdit}
            onClose={() => {
              setDialog(null);
              setHotelToEdit(null);
            }}
            onSaved={() => {
              setDialog(null);
              setHotelToEdit(null);
              queryClient.invalidateQueries({ queryKey: ["external-hotels"] });
            }}
            onDelete={(id) => {
              removeHotel.mutate(id);
              setDialog(null);
              setHotelToEdit(null);
            }}
          />
        )}

        {dialog === "edit_campus" && (
          <CampusLocationModal
            base={base}
            campus={campus}
            onClose={() => setDialog(null)}
            onSaved={() => {
              setDialog(null);
              queryClient.invalidateQueries({ queryKey: ["external-hotels"] });
            }}
          />
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------
// Interactive Leaflet Map Sub-component
// -------------------------------------------------------------

function StayLeafletMap({
  campus,
  radiusKm,
  hotels,
  selectedIds,
  highlightedId,
  mobileTab,
  onSelectHotel: _onSelectHotel,
  onHighlightHotel,
}: {
  campus: { name: string; latitude: number; longitude: number };
  radiusKm: number;
  hotels: ExternalHotelItem[];
  selectedIds: number[];
  highlightedId: number | null;
  mobileTab?: "list" | "map";
  onSelectHotel?: (id: number) => void;
  onHighlightHotel: (id: number) => void;
}) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  const markersRef = useRef<Record<number, L.Marker>>({});
  const circleRef = useRef<L.Circle | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);

  // Initialize Leaflet Map
  useEffect(() => {
    const container = mapContainerRef.current;
    if (!container) return;

    if ((container as any)._leaflet_id != null) {
      (container as any)._leaflet_id = null;
    }

    let instance: L.Map | null = null;
    try {
      instance = L.map(container, {
        center: [campus.latitude, campus.longitude],
        zoom: 12,
        scrollWheelZoom: true,
      });

      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(instance);

      setMap(instance);

      // Force size invalidation
      instance.invalidateSize();
      const timer = setTimeout(() => {
        instance?.invalidateSize();
      }, 250);

      const resizeObserver = new ResizeObserver(() => {
        instance?.invalidateSize();
      });
      resizeObserver.observe(container);

      return () => {
        clearTimeout(timer);
        resizeObserver.disconnect();
        instance?.remove();
        setMap(null);
      };
    } catch (err) {
      console.error("Leaflet init error:", err);
      setMapError("Map tiles could not load. You can continue using the list view.");
    }
  }, [campus.latitude, campus.longitude]);

  // Invalidate size on mobile tab switch
  useEffect(() => {
    if (mobileTab === "map" && map) {
      const timer = setTimeout(() => map.invalidateSize(), 150);
      return () => clearTimeout(timer);
    }
  }, [mobileTab, map]);

  // Update Campus Marker, Radius Circle, and Hotel Pins
  useEffect(() => {
    if (!map) return;

    map.invalidateSize();

    // 1. Campus Marker with distinct styling
    const campusIcon = L.divIcon({
      className: "custom-campus-pin-leaf",
      html: `<div class="campus-pin-badge">🎓 ${campus.name}</div>`,
      iconSize: [140, 36],
      iconAnchor: [70, 18],
    });

    const campusMarker = L.marker([campus.latitude, campus.longitude], {
      icon: campusIcon,
      zIndexOffset: 1000,
    })
      .addTo(map)
      .bindPopup(`<strong>${campus.name}</strong><br />Host Institution Campus`);

    // 2. Radius Circle
    if (circleRef.current) {
      circleRef.current.remove();
    }
    const circle = L.circle([campus.latitude, campus.longitude], {
      radius: radiusKm * 1000,
      color: "#4d84ff",
      fillColor: "#4d84ff",
      fillOpacity: 0.08,
      weight: 2,
      dashArray: "5 5",
    }).addTo(map);
    circleRef.current = circle;

    // 3. Clear existing hotel markers
    Object.values(markersRef.current).forEach((m) => m.remove());
    markersRef.current = {};

    const bounds = L.latLngBounds([[campus.latitude, campus.longitude]]);
    let validHotelsCount = 0;

    // 4. Add Hotel Markers
    hotels.forEach((hotel) => {
      if (typeof hotel.latitude !== "number" || typeof hotel.longitude !== "number") return;
      if (isNaN(hotel.latitude) || isNaN(hotel.longitude)) return;

      validHotelsCount++;
      const isSelected = selectedIds.includes(hotel.id);
      const isVerified = hotel.is_verified;

      const pinColor = isSelected ? "#06b6d4" : isVerified ? "#10b981" : "#f59e0b";
      const icon = L.divIcon({
        className: "custom-hotel-pin-leaf",
        html: `
          <div class="hotel-pin-wrapper ${isSelected ? "selected-pin" : ""}">
            <div class="hotel-pin-dot" style="background: ${pinColor}"></div>
            <div class="hotel-pin-label">${hotel.name.split(" ")[0]}</div>
          </div>
        `,
        iconSize: [60, 40],
        iconAnchor: [30, 7],
      });

      const marker = L.marker([hotel.latitude, hotel.longitude], {
        icon,
        zIndexOffset: isSelected ? 900 : isVerified ? 500 : 100,
      }).addTo(map);

      // Popup content
      const phoneHtml = hotel.phone
        ? `<p style="margin:4px 0;"><a href="tel:${hotel.phone}" style="color:#2563eb;font-weight:600;">📞 Call: ${hotel.phone}</a></p>`
        : `<p style="margin:4px 0;color:#888;">Phone not available</p>`;

      const popupHtml = `
        <div style="min-width:180px;font-family:sans-serif;font-size:13px;line-height:1.4;">
          <h4 style="margin:0 0 4px 0;font-size:14px;font-weight:700;">${hotel.name}</h4>
          <span style="display:inline-block;padding:2px 6px;border-radius:12px;font-size:11px;font-weight:600;background:${
            isVerified ? "#d1fae5" : "#fef3c7"
          };color:${isVerified ? "#065f46" : "#92400e"};">
            ${isVerified ? "✓ Verified" : "⚠️ Unverified"}
          </span>
          <p style="margin:4px 0;"><strong>${hotel.distance_km} km</strong> (~${hotel.travel_time_min} min by car)</p>
          <p style="margin:4px 0;color:#059669;font-weight:600;">${hotel.price_display}</p>
          <p style="margin:4px 0;color:#666;font-size:12px;">${hotel.address || hotel.area || ""}</p>
          ${phoneHtml}
          <div style="margin-top:8px;">
            <a href="${hotel.google_maps_link}" target="_blank" rel="noopener noreferrer" style="color:#4f46e5;text-decoration:underline;">
              Open in Google Maps ↗
            </a>
          </div>
        </div>
      `;

      marker.bindPopup(popupHtml);

      marker.on("click", () => {
        onHighlightHotel(hotel.id);
        const cardElem = document.getElementById(`hotel-card-${hotel.id}`);
        if (cardElem) {
          cardElem.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      });

      markersRef.current[hotel.id] = marker;
      bounds.extend([hotel.latitude, hotel.longitude]);
    });

    // Fit map view
    if (validHotelsCount > 0) {
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 14 });
    } else {
      map.setView([campus.latitude, campus.longitude], 12);
    }

    return () => {
      campusMarker.remove();
      circle.remove();
      Object.values(markersRef.current).forEach((m) => m.remove());
    };
  }, [map, campus.latitude, campus.longitude, campus.name, radiusKm, hotels, selectedIds]);

  // Handle external highlight
  useEffect(() => {
    if (!highlightedId || !markersRef.current[highlightedId] || !map) return;
    const marker = markersRef.current[highlightedId];
    map.panTo(marker.getLatLng(), { animate: true });
    marker.openPopup();
  }, [highlightedId, map]);

  if (mapError) {
    return (
      <div className="map-fallback-banner">
        <AlertTriangle size={24} className="warn-icon" />
        <p>{mapError}</p>
      </div>
    );
  }

  return (
    <div className="stay-map-container">
      <div ref={mapContainerRef} className="stay-leaflet-instance" />
      <div className="map-legend">
        <span className="legend-item"><span className="dot dot-campus" /> Campus</span>
        <span className="legend-item"><span className="dot dot-verified" /> Verified</span>
        <span className="legend-item"><span className="dot dot-unverified" /> Unverified</span>
        <span className="legend-item"><span className="dot dot-selected" /> Selected</span>
      </div>
    </div>
  );
}

// -------------------------------------------------------------
// Send To Participant Modal
// -------------------------------------------------------------

function SendToParticipantModal({
  base,
  selectedHotels,
  campusName,
  onClose,
  onSent,
}: {
  base: string;
  selectedHotels: ExternalHotelItem[];
  campusName: string;
  onClose: () => void;
  onSent: () => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedParticipant, setSelectedParticipant] = useState<ParticipantSearchResult | null>(null);
  const [confirmedUnverified, setConfirmedUnverified] = useState(false);
  const [message, setMessage] = useState("");
  const [copiedFallback, setCopiedFallback] = useState(false);

  const q = useDebounced(search, 200);

  // Search participants
  const participantsQuery = useQuery({
    queryKey: ["stay-participants", base, q],
    queryFn: () => api<{ items: ParticipantSearchResult[] }>(`${base}/external-stays/participants?q=${encodeURIComponent(q)}`),
  });

  // Check unverified hotels
  const unverifiedHotels = useMemo(
    () => selectedHotels.filter((h) => !h.is_verified),
    [selectedHotels]
  );
  const hasUnverified = unverifiedHotels.length > 0;

  // Pre-load default message template
  useEffect(() => {
    if (selectedHotels.length === 0) return;
    const lines = [
      `🏨 Outside Accommodation Suggestions — ${campusName}`,
      "Here are recommended nearby hotels for your stay during Aarohan 2026:\n",
    ];
    selectedHotels.forEach((h, idx) => {
      const vText = h.is_verified ? "✅ Verified" : "⚠️ Unverified (Confirm before booking)";
      const phoneText = h.phone || "Phone not available";
      lines.push(`${idx + 1}. ${h.name} (${vText})`);
      lines.push(`📍 Distance: ${h.distance_km} km from campus (~${h.travel_time_min} mins by car)`);
      lines.push(`💰 Price: ${h.price_display}`);
      lines.push(`📞 Phone: ${phoneText}`);
      if (h.google_maps_link) {
        lines.push(`🗺️ Google Maps: ${h.google_maps_link}`);
      }
      lines.push("");
    });
    lines.push("Note: Hotel stay is at your own cost and booking. Please confirm availability by calling the hotel.");
    setMessage(lines.join("\n"));
  }, [selectedHotels, campusName]);

  // Send action
  const send = useAction(
    async () => {
      if (!selectedParticipant) throw new Error("Select a participant first.");
      if (hasUnverified && !confirmedUnverified) {
        throw new Error("Please confirm the unverified hotel warning before dispatching.");
      }

      return await api<{
        success: boolean;
        delivery_status: string;
        telegram_status?: string;
        message: string;
        fallback_available?: boolean;
        preview_message?: string;
      }>(`${base}/external-stays/send`, {
        method: "POST",
        body: JSON.stringify({
          participant_id: selectedParticipant.id,
          hotel_ids: selectedHotels.map((h) => h.id),
          message_override: message,
          confirm_unverified: confirmedUnverified,
        }),
      });
    },
    {
      success: (res) => {
        if (res.success) {
          onSent();
          return res.message;
        }
        return res.message;
      },
    }
  );

  // Copy fallback action
  const copyFallback = useAction(
    async () => {
      if (!selectedParticipant) return;
      await navigator.clipboard.writeText(message);
      setCopiedFallback(true);

      // Log in audit/history
      await api(`${base}/external-stays/log-fallback-copied`, {
        method: "POST",
        body: JSON.stringify({
          participant_id: selectedParticipant.id,
          hotel_ids: selectedHotels.map((h) => h.id),
          message_text: message,
        }),
      });
    },
    {
      success: "Message copied to clipboard and logged to history!",
      onDone: onSent,
    }
  );

  return (
    <Dialog title="Send Stay Suggestions to Participant" onClose={onClose} className="dialog-wide">
      <div className="send-modal-flow">
        {/* Step 1: Participant Search */}
        <section className="modal-step">
          <label className="step-label">1. Search and Select Participant</label>
          <div className="search-wrap">
            <Search size={15} className="search-icon" />
            <input
              type="search"
              aria-label="Search participant"
              placeholder="Search by participant name, phone number, or code (e.g. P0001)..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="participant-select-list">
            <QueryState query={participantsQuery}>
              {participantsQuery.data?.items.map((p) => {
                const isSelected = selectedParticipant?.id === p.id;
                const isTelegramLinked = p.telegram_status === "linked";

                return (
                  <div
                    key={p.id}
                    className={`participant-item ${isSelected ? "selected" : ""}`}
                    onClick={() => setSelectedParticipant(p)}
                  >
                    <div className="p-info">
                      <div className="p-name-row">
                        <strong>{p.name}</strong> <span className="mono small">({p.code})</span>
                      </div>
                      <div className="p-details muted small">
                        <span>{p.college}</span>
                        {p.phone && <span>• {p.phone}</span>}
                        {p.arrival_at && <span>• Arriving {formatTime(p.arrival_at)}</span>}
                      </div>
                    </div>

                    <div className="p-tg-badge">
                      {isTelegramLinked ? (
                        <span className="tg-pill ok" title={`Chat ID: ${p.telegram_chat_id}`}>
                          <Send size={11} /> Telegram Linked
                        </span>
                      ) : (
                        <span className="tg-pill warn">
                          <AlertTriangle size={11} /> No Telegram Bot
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </QueryState>
          </div>
        </section>

        {/* Selected Participant Status Warning */}
        {selectedParticipant && selectedParticipant.telegram_status === "not_started" && (
          <div className="no-telegram-alert">
            <AlertTriangle size={18} className="warn-icon" />
            <div className="alert-content">
              <strong>This participant has not started the Telegram bot</strong>
              <p className="small muted">
                You can preview and copy the structured message below to send directly via SMS or WhatsApp.
              </p>
            </div>
            <button
              type="button"
              className="button-secondary small"
              disabled={copyFallback.isPending}
              onClick={() => copyFallback.mutate()}
            >
              <Copy size={13} /> {copiedFallback ? "Copied!" : "Copy message"}
            </button>
          </div>
        )}

        {/* Step 2: Unverified Hotels Warning */}
        {hasUnverified && (
          <div className="unverified-warning-box">
            <AlertTriangle size={18} className="warn-icon" />
            <div className="warn-text">
              <strong>Unverified Hotels Included ({unverifiedHotels.length})</strong>
              <p className="small">
                {unverifiedHotels.map((h) => h.name).join(", ")} have unconfirmed phone numbers or rates.
              </p>
              <label className="checkbox-confirm">
                <input
                  type="checkbox"
                  checked={confirmedUnverified}
                  onChange={(e) => setConfirmedUnverified(e.target.checked)}
                />
                <span>I confirm sending unverified hotel suggestions to this participant</span>
              </label>
            </div>
          </div>
        )}

        {/* Step 3: Message Preview & Edit */}
        <section className="modal-step">
          <div className="step-head">
            <label className="step-label">2. Message Preview (Editable)</label>
            <span className="muted small">Includes {selectedHotels.length} hotel(s) + Telegram location pins</span>
          </div>

          <textarea
            className="message-preview-textarea"
            rows={8}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </section>

        <ErrorNote error={send.error || copyFallback.error} />

        {/* Actions */}
        <div className="modal-actions-footer">
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>

          <button
            type="button"
            className="button-secondary"
            disabled={!selectedParticipant || copyFallback.isPending}
            onClick={() => copyFallback.mutate()}
            title="Copy formatted message to clipboard"
          >
            <Copy size={14} /> Copy message
          </button>

          {selectedParticipant?.telegram_status === "linked" && (
            <button
              type="button"
              className="button-primary"
              disabled={
                !selectedParticipant ||
                (hasUnverified && !confirmedUnverified) ||
                send.isPending
              }
              onClick={() => send.mutate()}
            >
              <Send size={14} /> {send.isPending ? "Sending..." : "Send via Telegram"}
            </button>
          )}
        </div>
      </div>
    </Dialog>
  );
}

// -------------------------------------------------------------
// Sent History Sub-component
// -------------------------------------------------------------

function StaySentHistory({ base }: { base: string }) {
  const [page, setPage] = useState(1);
  const historyQuery = useQuery({
    queryKey: ["stay-history", base, page],
    queryFn: () => api<{ items: StaySuggestionHistoryItem[]; total: number; pages: number }>(`${base}/external-stays/history?page=${page}&page_size=20`),
  });

  const columns = [
    {
      label: "Time",
      cell: (r: StaySuggestionHistoryItem) => (
        <span className="muted small">{formatTime(r.sent_at)}</span>
      ),
    },
    {
      label: "Volunteer",
      cell: (r: StaySuggestionHistoryItem) => <strong>{r.sent_by}</strong>,
    },
    {
      label: "Participant",
      cell: (r: StaySuggestionHistoryItem) => (
        <div>
          <span>{r.participant.name}</span>{" "}
          <span className="mono small muted">({r.participant.code})</span>
        </div>
      ),
    },
    {
      label: "Hotels Sent",
      cell: (r: StaySuggestionHistoryItem) => (
        <div className="sent-hotels-list">
          {r.hotel_names.map((name, i) => (
            <span key={i} className="hotel-tag">
              {name}
            </span>
          ))}
        </div>
      ),
    },
    {
      label: "Channel",
      cell: (r: StaySuggestionHistoryItem) => (
        <Pill
          value={r.delivery_channel === "telegram" ? "Telegram" : "Clipboard Copy"}
          tone={r.delivery_channel === "telegram" ? "ok" : "warn"}
        />
      ),
    },
    {
      label: "Status",
      cell: (r: StaySuggestionHistoryItem) => {
        const tone = r.delivery_status === "delivered" ? "ok" : r.delivery_status === "copied" ? "info" : "bad";
        return (
          <div>
            <Pill value={r.delivery_status} tone={tone} />
            {r.error_message && <div className="muted small text-error">{r.error_message}</div>}
          </div>
        );
      },
    },
  ];

  return (
    <div className="history-tab-body">
      <QueryState query={historyQuery}>
        {historyQuery.data && (
          <>
            <DataTable
              rows={historyQuery.data.items}
              columns={columns}
              rowKey={(r) => r.id}
              empty="No stay suggestions have been sent yet."
            />
            <Pagination
              page={page}
              pages={historyQuery.data.pages}
              total={historyQuery.data.total}
              onPage={setPage}
            />
          </>
        )}
      </QueryState>
    </div>
  );
}

// -------------------------------------------------------------
// Add / Edit Hotel Modal
// -------------------------------------------------------------

function HotelFormModal({
  base,
  campus,
  existing,
  onClose,
  onSaved,
  onDelete,
}: {
  base: string;
  campus: { latitude: number; longitude: number };
  existing?: ExternalHotelItem;
  onClose: () => void;
  onSaved: () => void;
  onDelete?: (id: number) => void;
}) {
  const [form, setForm] = useState({
    name: existing?.name ?? "",
    address: existing?.address ?? "",
    area: existing?.area ?? "",
    latitude: existing?.latitude ?? campus.latitude,
    longitude: existing?.longitude ?? campus.longitude,
    phone: existing?.phone ?? "",
    alternate_phone: existing?.alternate_phone ?? "",
    price_min: existing?.price_min ?? "",
    price_max: existing?.price_max ?? "",
    rating: existing?.rating ?? 4.0,
    notes: existing?.notes ?? "",
    google_maps_link: existing?.google_maps_link ?? "",
    is_verified: existing?.is_verified ?? false,
  });

  const save = useAction(
    async () => {
      const payload = {
        ...form,
        latitude: parseFloat(String(form.latitude)),
        longitude: parseFloat(String(form.longitude)),
        price_min: form.price_min ? parseInt(String(form.price_min)) : null,
        price_max: form.price_max ? parseInt(String(form.price_max)) : null,
        rating: form.rating ? parseFloat(String(form.rating)) : null,
      };

      if (existing) {
        return await api(`${base}/external-hotels/${existing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      }
      return await api(`${base}/external-hotels`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    {
      success: existing ? "Hotel updated successfully" : "Hotel added successfully",
      onDone: onSaved,
    }
  );

  return (
    <Dialog title={existing ? `Edit ${existing.name}` : "Add External Hotel"} onClose={onClose}>
      <form
        className="form form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="span-all">
          <Field label="Hotel name">
            <input
              required
              minLength={2}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Hotel Sunshine"
            />
          </Field>
        </div>

        <Field label="Area / Locality">
          <input
            value={form.area}
            onChange={(e) => setForm({ ...form, area: e.target.value })}
            placeholder="e.g. Rau Circle, AB Road"
          />
        </Field>

        <Field label="Phone number" hint="Tap-to-call for participants">
          <input
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
            placeholder="e.g. +91-731-2857000"
          />
        </Field>

        <Field label="Latitude">
          <input
            type="number"
            step="any"
            required
            value={form.latitude}
            onChange={(e) => setForm({ ...form, latitude: parseFloat(e.target.value) || 0 })}
          />
        </Field>

        <Field label="Longitude">
          <input
            type="number"
            step="any"
            required
            value={form.longitude}
            onChange={(e) => setForm({ ...form, longitude: parseFloat(e.target.value) || 0 })}
          />
        </Field>

        <Field label="Min price (₹/night)">
          <input
            type="number"
            min={0}
            value={form.price_min}
            onChange={(e) => setForm({ ...form, price_min: e.target.value })}
            placeholder="e.g. 1500"
          />
        </Field>

        <Field label="Max price (₹/night)">
          <input
            type="number"
            min={0}
            value={form.price_max}
            onChange={(e) => setForm({ ...form, price_max: e.target.value })}
            placeholder="e.g. 2800"
          />
        </Field>

        <div className="span-all">
          <Field label="Full address">
            <input
              value={form.address}
              onChange={(e) => setForm({ ...form, address: e.target.value })}
              placeholder="Full street address..."
            />
          </Field>
        </div>

        <div className="span-all">
          <Field label="Google Maps link">
            <input
              type="url"
              value={form.google_maps_link}
              onChange={(e) => setForm({ ...form, google_maps_link: e.target.value })}
              placeholder="https://maps.google.com/?q=..."
            />
          </Field>
        </div>

        <div className="span-all">
          <Field label="Notes & amenities">
            <textarea
              rows={2}
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              placeholder="e.g. 24/7 check-in, AC rooms, elevator..."
            />
          </Field>
        </div>

        <div className="span-all">
          <label className="checkbox-chip">
            <input
              type="checkbox"
              checked={form.is_verified}
              onChange={(e) => setForm({ ...form, is_verified: e.target.checked })}
            />
            <span>Mark as Verified (Phone and pricing confirmed by volunteer)</span>
          </label>
        </div>

        <div className="span-all">
          <ErrorNote error={save.error} />
          <div className="form-actions-split">
            {existing && onDelete && (
              <button
                type="button"
                className="button-ghost danger"
                onClick={() => {
                  if (confirm(`Remove ${existing.name}?`)) {
                    onDelete(existing.id);
                  }
                }}
              >
                <Trash2 size={14} /> Delete
              </button>
            )}
            <button type="submit" className="button-primary" disabled={save.isPending}>
              {save.isPending ? "Saving..." : existing ? "Save changes" : "Add hotel"}
            </button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}

// -------------------------------------------------------------
// Campus Location Modal
// -------------------------------------------------------------

function CampusLocationModal({
  base,
  campus,
  onClose,
  onSaved,
}: {
  base: string;
  campus: { name: string; latitude: number; longitude: number };
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    campus_name: campus.name,
    campus_latitude: campus.latitude,
    campus_longitude: campus.longitude,
  });

  const save = useAction(
    () =>
      api(`${base}/campus-coordinates`, {
        method: "PATCH",
        body: JSON.stringify({
          ...form,
          campus_latitude: parseFloat(String(form.campus_latitude)),
          campus_longitude: parseFloat(String(form.campus_longitude)),
        }),
      }),
    {
      success: "Campus coordinates updated",
      onDone: onSaved,
    }
  );

  return (
    <Dialog title="Edit Campus Coordinates" onClose={onClose}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p className="muted small">
          These coordinates are used as the fixed reference point for calculating hotel distances and driving times.
        </p>
        <Field label="Campus name">
          <input
            required
            value={form.campus_name}
            onChange={(e) => setForm({ ...form, campus_name: e.target.value })}
          />
        </Field>
        <Field label="Campus Latitude">
          <input
            type="number"
            step="any"
            required
            value={form.campus_latitude}
            onChange={(e) => setForm({ ...form, campus_latitude: parseFloat(e.target.value) || 0 })}
          />
        </Field>
        <Field label="Campus Longitude">
          <input
            type="number"
            step="any"
            required
            value={form.campus_longitude}
            onChange={(e) => setForm({ ...form, campus_longitude: parseFloat(e.target.value) || 0 })}
          />
        </Field>
        <ErrorNote error={save.error} />
        <button type="submit" className="button-primary" disabled={save.isPending}>
          Save campus location
        </button>
      </form>
    </Dialog>
  );
}
