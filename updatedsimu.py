import streamlit as st
from urllib.parse import urlparse, parse_qs
import pandas as pd
import numpy as np
import folium
from folium.plugins import AntPath
from streamlit_folium import st_folium
from sklearn.cluster import KMeans
import json, requests, io
import matplotlib as mpl
from math import ceil, radians, sin, cos, sqrt, atan2
import os
import osmnx as ox
import networkx as nx
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

# PAGE CONFIG
st.set_page_config(page_title="Cascais —Waste Management Simulation", layout="wide")

# CONSTANTS VARIABLES
DEPOT = (38.7374, -9.3391)
CAPACITY_TRUCK_KG = 10000
CAPACITY_BIN_KG = 250
DIESEL_L_PER_KM = 0.35
DIESEL_CO2_PER_L = 2.68
ELEC_KWH_PER_KM = 1.2
ELEC_CO2_PER_KWH = 0.15
BASE_GROWTH_MUNICIPAL_PCT_PER_DAY = 10   # municipal bins baseline daily % increase (tunable)
BASE_GROWTH_TEXTILE_PCT_PER_DAY  = 3    # textile bins baseline daily % increase (tunable)
TEXTILE_BIN_CAPACITY_KG = 100
TEXTILE_CO2_SAVING_KG_PER_KG_RECYCLED = 4.0  # CO2 saving if textile recycled
TEXTILE_LAT_MIN, TEXTILE_LAT_MAX = 38.68, 38.72
TEXTILE_LON_MIN, TEXTILE_LON_MAX = -9.45, -9.35
CLIMATE_NEUTRAL_GOAL = 0.0  # kg CO2 target
FLEET_COLORS = {"Diesel": "#FF6600", "Electric": "#33CC33"}

def get_live_weather():
    """Daily forecast for Cascais area, temp max & precip."""
    url = "https://api.open-meteo.com/v1/forecast?latitude=38.7&longitude=-9.4&daily=temperature_2m_max,precipitation_sum&timezone=auto"
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        data = r.json()
        return pd.DataFrame({
            "date": data["daily"]["time"],
            "temp_max_c": data["daily"]["temperature_2m_max"],
            "precip_mm": data["daily"]["precipitation_sum"]
        })
    return pd.DataFrame(columns=["date", "temp_max_c", "precip_mm"])

def compute_climate_factor(temp_c: float, rain_mm: float) -> float:
    """Hotter increases growth, rain reduces it. Clamped to [0.7, 1.4]."""
    factor = 1 + 0.015 * (temp_c - 18.0)
    if rain_mm >= 15:
        factor *= 0.8
    elif rain_mm >= 5:
        factor *= 0.9
    return float(np.clip(factor, 0.7, 1.4))

def climate_tint_rgba(factor: float) -> str:
    """Color overlay for map by climate intensity."""
    if factor >= 1.15:
        return "rgba(255, 80, 0, 0.22)"    # hot
    if factor >= 1.05:
        return "rgba(255, 160, 0, 0.16)"   # warm
    if factor <= 0.85:
        return "rgba(0, 100, 255, 0.22)"   # cold
    if factor <= 0.95:
        return "rgba(0, 150, 255, 0.16)"   # cool
    return "rgba(255,255,255,0.0)"         # neutral

def co2_for_distance_km(distance_km: float, truck_type: str) -> float:
    if truck_type.lower() == "diesel":
        return distance_km * DIESEL_L_PER_KM * DIESEL_CO2_PER_L
    return distance_km * ELEC_KWH_PER_KM * ELEC_CO2_PER_KWH

def load_geojson_bins(path="bins_latest_fixed.geojson") -> pd.DataFrame:
    """Load your municipal bins GeoJSON (the one used in Mapbox HTML)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    recs = []
    for ft in data["features"]:
        p = ft.get("properties", {})
        g = ft.get("geometry", {})
        if not g or g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        recs.append({
            "bin_id": p.get("id"),
            "localidade": p.get("localidade", "N/A"),
            "rua": p.get("rua", "N/A"),
            "type": p.get("type", "ind"),
            "fill_now": float(p.get("fill_level", 0.0)),
            "longitude": float(lon),
            "latitude": float(lat),
        })
    df = pd.DataFrame(recs).dropna(subset=["latitude", "longitude"])
    return df

def simulate_textile_bins(n=100,
                          base_growth_pct_per_day=BASE_GROWTH_TEXTILE_PCT_PER_DAY,
                          temp_c=22.0, rain_mm=0.0, tourism_factor=1.0,
                          seed=42):
    """Synthetic textile bins in Cascais with climate+tourism-sensitive growth."""
    np.random.seed(seed)
    lat = np.random.uniform(TEXTILE_LAT_MIN, TEXTILE_LAT_MAX, n)
    lon = np.random.uniform(TEXTILE_LON_MIN, TEXTILE_LON_MAX, n)

    zones = np.random.choice(["Urban", "Coastal", "Residential"], size=n, p=[0.45, 0.3, 0.25])
    zone_bias = np.where(zones == "Urban", 1.1, np.where(zones == "Coastal", 1.05, 0.95))
    fill_now = np.clip(np.random.normal(35, 15, n), 5, 80)

    climate_factor = compute_climate_factor(temp_c, rain_mm)
    tourism_mult = 1.0 + 0.3*(float(tourism_factor) - 1.0)

    future = fill_now + base_growth_pct_per_day * climate_factor * tourism_mult * zone_bias
    future = np.clip(future, 0, 100)

    df = pd.DataFrame({
        "bin_id": [f"TX-{i+1}" for i in range(n)],
        "localidade": "Cascais",
        "rua": "Textile Zone",
        "type": "textile",
        "latitude": lat,
        "longitude": lon,
        "zone": zones,
        "fill_now": fill_now,
        "fill_future": future
    })

    recycled_kg = (df["fill_future"] / 100.0) * TEXTILE_BIN_CAPACITY_KG
    df["recycled_kg"] = recycled_kg
    df["co2_saved_kg"] = recycled_kg * TEXTILE_CO2_SAVING_KG_PER_KG_RECYCLED
    return df

def export_df_to_geojson(df: pd.DataFrame) -> bytes:
    """Export (lat,lon) points to GeoJSON FeatureCollection."""
    features = []
    for _, r in df.iterrows():
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]},
            "properties": {
                "id": str(r.get("bin_id", "")),
                "localidade": r.get("localidade", ""),
                "rua": r.get("rua", ""),
                "type": r.get("type", ""),
                "fill_now": float(r.get("fill_now", 0.0)),
                "fill_future": float(r.get("fill_future", r.get("fill_now", 0.0))),
                "zone": r.get("zone", "")
            }
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, ensure_ascii=False).encode("utf-8")

# OSMnx + OR-Tools helpers (offline routing)
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

@st.cache_resource
def get_cascais_graph():
    cache_path = "cascais_graph.graphml"
    if os.path.exists(cache_path):
        return ox.load_graphml(cache_path)
    st.info("Downloading Cascais road network (first time only)...")
    G = ox.graph_from_place("Cascais, Portugal", network_type="drive")
    ox.save_graphml(G, cache_path)
    return G

def compute_distance_matrix(G, coords_latlon):
    nodes = [ox.distance.nearest_nodes(G, lon, lat) for lat, lon in coords_latlon]
    n = len(nodes)
    matrix = [[0.0]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1, n):
            try:
                dist_m = nx.shortest_path_length(G, nodes[i], nodes[j], weight="length")
                km = dist_m / 1000.0
            except nx.NetworkXNoPath:
                lat1, lon1 = coords_latlon[i]
                lat2, lon2 = coords_latlon[j]
                km = haversine_km(lat1, lon1, lat2, lon2)
            matrix[i][j] = matrix[j][i] = km
    return matrix

def solve_tsp_ortools(dist_matrix):
    n = len(dist_matrix)
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)  # single vehicle for cluster TSP
    routing = pywrapcp.RoutingModel(manager)
    def distance_callback(from_idx, to_idx):
        a, b = manager.IndexToNode(from_idx), manager.IndexToNode(to_idx)
        return int(dist_matrix[a][b] * 1000)  # meters as int
    cb = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = 5
    solution = routing.SolveWithParameters(params)
    if not solution:
        return list(range(n))
    order = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        order.append(manager.IndexToNode(idx))
        idx = solution.Value(routing.NextVar(idx))
    order.append(manager.IndexToNode(idx))
    return order


# LAYOUT HEADER
st.markdown("## Waste Collection & Textile Simulation")
st.caption("EMAC – Cascais Ambiente")

# TOP-LEVEL MODE
mode = st.sidebar.radio("Module", ["Waste Collection (Municipal Waste)", "Textile Simulation"])

# SHARED: WEATHER PANEL
st.sidebar.markdown("### Weather")
weather_mode = st.sidebar.radio("Weather Source", ["Live (Open-Meteo)", "Manual"], horizontal=True)
if weather_mode == "Live (Open-Meteo)":
    wdf = get_live_weather()
    temp_c = float(wdf.iloc[0]["temp_max_c"]) if not wdf.empty else 22.0
    rain_mm = float(wdf.iloc[0]["precip_mm"]) if not wdf.empty else 0.0
else:
    temp_c = st.sidebar.slider("Temperature (°C)", 0, 40, 24, 1)
    rain_mm = st.sidebar.slider("Rainfall (mm)", 0.0, 30.0, 0.0, 0.5)

climate_factor = compute_climate_factor(temp_c, rain_mm)
st.markdown(
    f"""
<div style='background:rgba(248,248,255,0.95); padding:10px 14px; border-radius:10px;'>
   <b>Temperature:</b> {temp_c:.1f}°C &nbsp;|&nbsp;
   <b>Rain:</b> {rain_mm:.1f} mm &nbsp;|&nbsp;
   <b>Climate Factor:</b> {climate_factor:.2f}
</div>
""",
    unsafe_allow_html=True,
)

# MODULE 1: Waste Collection (Municipal Waste — Offline Dual Fleet + Climate)
if mode == "Waste Collection (Municipal Waste)":
    st.subheader("Municipal Waste — with Climate Impact")

    # Load municipal bins (uploaded or local)
    left, right = st.columns([1, 1])
    with left:
        st.markdown("**Data file** (GeoJSON with bins)")
        uploaded = st.file_uploader(
            "Upload bins GeoJSON (optional)", type=["geojson", "json"], label_visibility="collapsed"
        )

    if uploaded:
        data_bytes = uploaded.read()
        data = json.loads(data_bytes.decode("utf-8"))
        recs = []
        for ft in data["features"]:
            p = ft.get("properties", {})
            g = ft.get("geometry", {})
            if not g or g.get("type") != "Point":
                continue
            lon, lat = g["coordinates"][:2]
            recs.append({
                "bin_id": p.get("id"),
                "localidade": p.get("localidade", "N/A"),
                "rua": p.get("rua", "N/A"),
                "type": p.get("type", "ind"),
                "fill_now": float(p.get("fill_level", p.get("fill_now", 0.0))),
                "longitude": float(lon),
                "latitude": float(lat),
            })
        bins_df = pd.DataFrame(recs).dropna(subset=["latitude", "longitude"])
    else:
        bins_df = load_geojson_bins("bins_latest_fixed.geojson")

    # Scenario controls
    right.markdown("**Scenario**")
    days_ahead = right.slider("Days ahead (forecast)", 0, 7, 2)
    threshold_pct = right.slider("Collection threshold (%)", 50, 100, 80, 5)
    fleet_mode = right.radio("Fleet Mode", ["Single Type", "Dual Fleet"], horizontal=True)

    # Apply climate-aware forecast
    bins_df = bins_df.copy()
    bins_df["fill_now"] = bins_df["fill_now"].clip(0, 130)
    baseline_growth = days_ahead * BASE_GROWTH_MUNICIPAL_PCT_PER_DAY
    climate_growth = days_ahead * BASE_GROWTH_MUNICIPAL_PCT_PER_DAY * climate_factor
    bins_df["fill_future"] = (bins_df["fill_now"] + climate_growth).clip(0, 130)

    bins_df["ΔClimateImpact (%)"] = (
        (bins_df["fill_now"] + climate_growth) - (bins_df["fill_now"] + baseline_growth)
    ) / (bins_df["fill_now"] + baseline_growth + 1e-6) * 100.0

    # Select bins meeting threshold
    to_collect = bins_df[
        (bins_df["fill_future"] if days_ahead > 0 else bins_df["fill_now"]) >= threshold_pct
    ]
    
    # TRUCK CAPACITY ESTIMATION & VALIDATION    
    if not to_collect.empty:
        to_collect["bin_weight_kg"] = (to_collect["fill_future"] / 100.0) * CAPACITY_BIN_KG
        total_weight = to_collect["bin_weight_kg"].sum()
        required_trucks = ceil(total_weight / CAPACITY_TRUCK_KG) if CAPACITY_TRUCK_KG > 0 else 0

        st.markdown(
            f"**Estimated total waste:** {total_weight:,.0f} kg  "
            f"•  **Truck capacity:** {CAPACITY_TRUCK_KG:,} kg/truck  "
            f"•  **Minimum trucks required:** {required_trucks}"
        )

        # Determine available trucks
        if fleet_mode == "Single Type":
            available_trucks = right.slider("Available trucks", 1, 12, 3)
        else:
            total_trucks = right.slider("Total trucks", 2, 12, 4)
            available_trucks = total_trucks

        # Compare capacity vs availability
        if required_trucks > available_trucks:
            st.warning(
                f" Available trucks ({available_trucks}) are fewer than required ({required_trucks}). "
                f"Some routes may exceed truck capacity or bins may remain uncollected."
            )
        else:
            st.success("Fleet capacity is sufficient for this scenario.")

    
    # Routing Function
    def cluster_and_route_offline(df_points: pd.DataFrame, n_clusters: int, fleet_assignments=None):
        if len(df_points) == 0:
            return 0.0, None, {}

        labels = KMeans(
            n_clusters=min(n_clusters, len(df_points)), random_state=42
        ).fit_predict(df_points[["latitude", "longitude"]])
        df_points = df_points.copy()
        df_points["cluster"] = labels

        m = folium.Map(
            location=[df_points["latitude"].mean(), df_points["longitude"].mean()],
            zoom_start=13,
            tiles="cartodbpositron",
        )
        folium.Marker(DEPOT, icon=folium.Icon(color="blue"), popup="Depot").add_to(m)

        # Legend
        legend_html = """
        <div style="position: fixed; bottom: 20px; left: 20px; width: 180px; height: 70px;
             border:2px solid grey; z-index:9999; font-size:14px;
             background-color:white; padding:8px; border-radius:10px;">
        <b>Fleet Legend</b><br>
        <i style="background:#FF6600; width:10px; height:10px; float:left; margin-right:5px;"></i> Diesel<br>
        <i style="background:#33CC33; width:10px; height:10px; float:left; margin-right:5px;"></i> Electric
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))

        # Climate tint
        tint = climate_tint_rgba(climate_factor)
        folium.Rectangle(
            bounds=[
                [df_points["latitude"].mean() - 0.12, df_points["longitude"].mean() - 0.12],
                [df_points["latitude"].mean() + 0.12, df_points["longitude"].mean() + 0.12],
            ],
            color=tint,
            fill=True,
            fill_opacity=0.30,
            weight=0,
        ).add_to(m)

        G = get_cascais_graph()
        total_km = 0.0
        cluster_distances = {}

        for i in sorted(df_points["cluster"].unique()):
            sub = df_points[df_points["cluster"] == i]

            # Cluster load check
            if "bin_weight_kg" in sub.columns:
                cluster_load = sub["bin_weight_kg"].sum()
                if cluster_load > CAPACITY_TRUCK_KG:
                    st.warning(
                        f" Cluster {i+1} exceeds capacity: {cluster_load:.0f} kg "
                        f"(limit {CAPACITY_TRUCK_KG:,} kg)"
                    )
                else:
                    st.caption(f"Cluster {i+1}: {cluster_load:.0f} kg collected (within capacity)")

            coords_latlon = [(DEPOT[0], DEPOT[1])] + [
                (float(r.latitude), float(r.longitude)) for _, r in sub.iterrows()
            ] + [(DEPOT[0], DEPOT[1])]

            dist_matrix = compute_distance_matrix(G, coords_latlon)
            order = solve_tsp_ortools(dist_matrix)
            ordered_coords = [coords_latlon[k] for k in order]
            cluster_dist = sum(
                dist_matrix[order[k]][order[k + 1]] for k in range(len(order) - 1)
            )
            total_km += cluster_dist
            cluster_distances[i] = cluster_dist

            truck_type = (
                fleet_assignments.get(i, "Diesel") if fleet_assignments else "Diesel"
            )
            color = FLEET_COLORS.get(truck_type, "#888888")

            # Route polyline
            route_line = []
            nodes = [ox.distance.nearest_nodes(G, lon, lat) for lat, lon in ordered_coords]
            for u, v in zip(nodes[:-1], nodes[1:]):
                try:
                    path = nx.shortest_path(G, u, v, weight="length")
                    seg = [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in path]
                    if route_line and seg and route_line[-1] == seg[0]:
                        route_line.extend(seg[1:])
                    else:
                        route_line.extend(seg)
                except nx.NetworkXNoPath:
                    continue

            if route_line:
                folium.PolyLine(route_line, color=color, weight=5, opacity=0.9,
                                popup=f"Cluster {i+1} — {truck_type}").add_to(m)
                AntPath(route_line, color=color, delay=1000, dash_array=[10, 20],
                        weight=4, opacity=0.7).add_to(m)

            for _, r in sub.iterrows():
                folium.CircleMarker(
                    (r.latitude, r.longitude),
                    radius=6,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.9,
                    popup=f"{r.get('bin_id','N/A')} — {truck_type}",
                ).add_to(m)

        return total_km, m, cluster_distances

    
    # FLEET ROUTING EXECUTION
    if to_collect.empty:
        st.info("No bins meet the collection threshold for this scenario.")
    else:
        if fleet_mode == "Single Type":
            truck_type = right.radio("Truck Type", ["Diesel", "Electric"], horizontal=True)
            total_km, fmap, cluster_dists = cluster_and_route_offline(to_collect, available_trucks)
            total_co2 = co2_for_distance_km(total_km, truck_type)
            st_folium(fmap, width=1000, height=560)
            st.success(f"Total Distance: **{total_km:.2f} km** — Estimated CO₂: **{total_co2:.2f} kg** ({truck_type})")

        else:
            diesel_share = right.slider("Diesel trucks (%)", 0, 100, 50, 10)
            diesel_trucks = int(available_trucks * diesel_share / 100)
            electric_trucks = available_trucks - diesel_trucks
            clusters = list(range(available_trucks))
            fleet_assignments = {
                cid: "Diesel" if idx < diesel_trucks else "Electric" for idx, cid in enumerate(clusters)
            }

            total_km, fmap, cluster_dists = cluster_and_route_offline(to_collect, available_trucks, fleet_assignments)
            diesel_km = sum(d for cid, d in cluster_dists.items() if fleet_assignments[cid] == "Diesel")
            electric_km = sum(d for cid, d in cluster_dists.items() if fleet_assignments[cid] == "Electric")
            diesel_co2 = co2_for_distance_km(diesel_km, "Diesel")
            electric_co2 = co2_for_distance_km(electric_km, "Electric")
            total_co2 = diesel_co2 + electric_co2

            st_folium(fmap, width=1000, height=560)
            st.markdown(f"""
            **Fleet summary:**
            -  Diesel: {diesel_trucks} trucks — {diesel_km:.1f} km — {diesel_co2:.1f} kg CO₂  
            -  Electric: {electric_trucks} trucks — {electric_km:.1f} km — {electric_co2:.1f} kg CO₂  
            -  **Total:** {total_km:.1f} km — {total_co2:.1f} kg CO₂
            """)
            if total_co2 <= CLIMATE_NEUTRAL_GOAL:
                st.success(" Climate Neutral! CO₂ target reached.")
            else:
                offset = total_co2 - CLIMATE_NEUTRAL_GOAL
                st.warning(f" Offset **{offset:.1f} kg CO₂** to reach climate neutrality.")

    
    # TABLE + EXPORT (Filtered Scenario Only)
    st.markdown("### Bins (with climate impact — collected / threshold met)")

    filtered_df = (
        to_collect[["bin_id", "localidade", "rua", "type", "fill_now", "fill_future", "ΔClimateImpact (%)"]]
        .round(2)
        .sort_values("fill_future", ascending=False)
    )

    if filtered_df.empty:
        st.info("No bins met the collection threshold or forecast criteria.")
    else:
        st.dataframe(
            filtered_df.reset_index(drop=True)
            .assign(No=lambda x: range(1, len(x) + 1))
            .set_index("No"),
            use_container_width=True,
        )

    colA, _ = st.columns(2)
    with colA:
        if st.button("Export Scenario Bins as GeoJSON"):
            geo_bytes = export_df_to_geojson(to_collect)
            st.download_button(
                "Download Filtered GeoJSON",
                data=geo_bytes,
                file_name="bins_scenario_forecast.geojson",
                mime="application/geo+json",
            )


# MODULE 2: TEXTILE SIMULATION (manual placement version)
else:
    st.subheader("Textile Simulation — Manual Placement & Circular Impact")

    # Controls
    col1, col2, col3 = st.columns(3)
    with col1:
        n_textile = st.number_input("Number of textile bins", 1, 50, 3, 1)
        tourism_factor = st.slider("Tourism impact (×)", 0.5, 2.0, 1.2, 0.1)
    with col2:
        collection_days = st.slider("Collection interval (days)", 1, 14, 7)
        recycle_eff = st.slider("Recycling efficiency (%)", 50, 100, 85, 1) / 100.0
    with col3:
        base_growth_textile = st.slider("Base growth per day (%)", 1, 10, BASE_GROWTH_TEXTILE_PCT_PER_DAY, 1)

    #  Manual textile bin input
    st.markdown("#### Define textile bin locations manually")

    manual_bins = []
    for i in range(int(n_textile)):
        st.markdown(f"**Bin {i+1}**")
        col1, col2, col3 = st.columns(3)
        with col1:
            lat = st.number_input(
                f"Latitude (Bin {i+1})",
                38.68, 38.76,
                38.70 + i * 0.001,
                0.0001,
                key=f"lat_{i}"
            )
        with col2:
            lon = st.number_input(
                f"Longitude (Bin {i+1})",
                -9.50, -9.30,
                -9.40 - i * 0.001,
                0.0001,
                key=f"lon_{i}"
            )
        with col3:
            fill = st.slider(
                f"Initial fill (%) — Bin {i+1}",
                0, 100, 40, 5,
                key=f"fill_slider_{i}"
            )

        manual_bins.append({
            "latitude": lat,
            "longitude": lon,
            "fill_now": fill
        })

    # Simulate textile bins using manual coordinates
    df_tx = pd.DataFrame(manual_bins)

    if df_tx.empty:
        st.warning("Please define at least one textile bin to simulate.")
    else:
        # Climate + tourism influence
        zones = np.random.choice(["Urban", "Coastal", "Residential"], size=len(df_tx), p=[0.45, 0.3, 0.25])
        df_tx["zone"] = zones

        climate_factor_tx = climate_factor  # reuse computed factor
        df_tx["fill_future"] = (
            df_tx["fill_now"] +
            base_growth_textile * (collection_days / 7.0) *
            climate_factor_tx * (1.0 + 0.3 * (tourism_factor - 1.0))
        ).clip(0, 100)

        recycled_kg = (df_tx["fill_future"] / 100.0) * TEXTILE_BIN_CAPACITY_KG
        df_tx["recycled_kg"] = recycled_kg * recycle_eff
        df_tx["co2_saved_kg"] = recycled_kg * recycle_eff * TEXTILE_CO2_SAVING_KG_PER_KG_RECYCLED
        df_tx["bin_id"] = [f"TX-{i+1}" for i in range(len(df_tx))]
        df_tx["type"] = "textile"

        #  Map Visualization
        m2 = folium.Map(
            location=[df_tx["latitude"].mean(), df_tx["longitude"].mean()],
            zoom_start=13,
            tiles="cartodbpositron"
        )
        for _, r in df_tx.iterrows():
            col = {"Urban": "#6f42c1", "Coastal": "#a34abf", "Residential": "#8a63d2"}.get(r["zone"], "#7f60c9")
            popup = f"""
            <b>{r['bin_id']}</b> — Textile ({r['zone']})<br>
            Now: {r['fill_now']:.1f}% • Future: {r['fill_future']:.1f}%<br>
            Recycled: {r['recycled_kg']:.1f} kg<br>
            CO₂ saved: {r['co2_saved_kg']:.1f} kg
            """
            folium.CircleMarker(
                (r["latitude"], r["longitude"]),
                radius=6,
                color=col,
                fill=True,
                fill_color=col,
                fill_opacity=0.95,
                popup=popup
            ).add_to(m2)

        # Optional climate tint overlay for textile context
        tint = climate_tint_rgba(climate_factor_tx)
        folium.Rectangle(
            bounds=[
                [df_tx["latitude"].min() - 0.01, df_tx["longitude"].min() - 0.01],
                [df_tx["latitude"].max() + 0.01, df_tx["longitude"].max() + 0.01]
            ],
            color=tint,
            fill=True,
            fill_opacity=0.30,
            weight=0
        ).add_to(m2)

        st_folium(m2, width=1000, height=560)

        #  Metrics
        total_recycled = df_tx["recycled_kg"].sum()
        total_saved_co2 = df_tx["co2_saved_kg"].sum()
        overflow_risk = (df_tx["fill_future"] >= 95).mean() * 100.0

        a, b, c = st.columns(3)
        a.metric(" Recycled textile (kg)", f"{total_recycled:,.0f}")
        b.metric(" CO₂ saved (kg)", f"{total_saved_co2:,.0f}")
        c.metric(" Overflow risk (%)", f"{overflow_risk:.1f}")

        #  Optional routing for textile collection
        st.markdown("###  Textile collection routing (optional)")
        enable_tx_routing = st.checkbox("Enable routing for textile bins over threshold", value=False)
        tx_threshold = st.slider("Textile threshold (%)", 50, 100, 70, 5)
        tx_trucks = st.slider("Textile trucks (clusters)", 1, 10, 3)

        if enable_tx_routing:
            tx_collect = df_tx[df_tx["fill_future"] >= tx_threshold]
            if tx_collect.empty:
                st.info("No simulated textile bins exceed the threshold.")
            else:
                # Reuse offline routing helpers for textile as well
                def cluster_and_route_textile(df_points: pd.DataFrame, n_clusters: int):
                    labels = KMeans(n_clusters=min(n_clusters, len(df_points)), random_state=42).fit_predict(
                        df_points[["latitude", "longitude"]]
                    )
                    df_points = df_points.copy()
                    df_points["cluster"] = labels

                    m = folium.Map(location=[df_points["latitude"].mean(), df_points["longitude"].mean()],
                                   zoom_start=13, tiles="cartodbpositron")
                    folium.Marker(DEPOT, icon=folium.Icon(color="blue"), popup="Depot").add_to(m)

                    G = get_cascais_graph()
                    total_km = 0.0

                    for i in sorted(df_points["cluster"].unique()):
                        sub = df_points[df_points["cluster"] == i]
                        coords_latlon = [(DEPOT[0], DEPOT[1])] + [
                            (float(r.latitude), float(r.longitude)) for _, r in sub.iterrows()
                        ] + [(DEPOT[0], DEPOT[1])]

                        dist_matrix = compute_distance_matrix(G, coords_latlon)
                        order = solve_tsp_ortools(dist_matrix)
                        ordered_coords = [coords_latlon[k] for k in order]
                        cluster_dist = sum(dist_matrix[order[k]][order[k+1]] for k in range(len(order)-1))
                        total_km += cluster_dist

                        color = "#4e79a7"

                        # Build polyline
                        route_line = []
                        nodes = [ox.distance.nearest_nodes(G, lon, lat) for lat, lon in ordered_coords]
                        for u, v in zip(nodes[:-1], nodes[1:]):
                            try:
                                path = nx.shortest_path(G, u, v, weight="length")
                                seg = [(G.nodes[n]["y"], G.nodes[n]["x"]) for n in path]
                                if route_line and seg and route_line[-1] == seg[0]:
                                    route_line.extend(seg[1:])
                                else:
                                    route_line.extend(seg)
                            except nx.NetworkXNoPath:
                                continue

                        if route_line:
                            folium.PolyLine(route_line, color=color, weight=5, opacity=0.9,
                                            popup=f"Cluster {i+1}").add_to(m)

                        for _, r in sub.iterrows():
                            folium.CircleMarker(
                                (r.latitude, r.longitude),
                                radius=6,
                                color=color, fill=True, fill_color=color, fill_opacity=0.95
                            ).add_to(m)

                    return total_km, m

                dist_tx, tx_map = cluster_and_route_textile(tx_collect, n_clusters=tx_trucks)
                st_folium(tx_map, width=1000, height=520)

                tx_truck_type = st.radio("Truck type for textile routing", ["Diesel", "Electric"], horizontal=True, index=1)
                co2_tx = co2_for_distance_km(dist_tx, tx_truck_type)
                st.success(f"Textile route distance: **{dist_tx:.2f} km** — CO₂: **{co2_tx:.2f} kg** ({tx_truck_type})")

        #  Table + Export
        st.markdown("###  Textile table")
        st.dataframe(
            df_tx[["bin_id", "zone", "fill_now", "fill_future", "recycled_kg", "co2_saved_kg"]].round(2),
            use_container_width=True
        )

        geo_bytes = export_df_to_geojson(df_tx)
        st.download_button(
            " Export Textile Simulation as GeoJSON",
            data=geo_bytes,
            file_name="textile_simulation.geojson",
            mime="application/geo+json"
        )
