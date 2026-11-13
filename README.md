# Cascais Digital Twin

A local **waste management Digital Twin** for Cascais, developed as part of research work carried out for the **CLIMABOROUGH project** within **Institut Mines-Télécom (IMT)/ DICE TSP**.

The project includes:

- 3D waste-bin visualization using Mapbox GL JS
- Waste-fill forecasting
- Municipal waste collection simulation
- Textile waste simulation
- Route optimization using the **Traveling Salesman Problem (TSP)**
- Road-network routing using OSMnx and NetworkX
- TSP optimization using Google OR-Tools
- Docker-based local deployment

## Research Context

This repository contains research and development work carried out in the context of the **CLIMABOROUGH project**.

The work focuses on the development and evaluation of Digital Twin-based approaches for urban waste management, including waste monitoring, forecasting, simulation, decision support, and optimized collection routing.

The implementation was developed within research activities associated with **TSP / Institut Mines-Télécom (IMT)**.

## Prerequisites

Before running the project, make sure you have:

- Docker installed
- A Mapbox account and public access token for the 3D map

## Mapbox Token Setup

The 3D map uses **Mapbox GL JS** and requires a Mapbox public access token.

1. Create a Mapbox account at [https://www.mapbox.com/](https://www.mapbox.com/)
2. Go to **Account → Access Tokens**.
3. Create or copy a public token beginning with `pk.`.
4. Open `test11.html`.
5. Find the following line:

```javascript
mapboxgl.accessToken = 'YOUR_MAPBOX_PUBLIC_TOKEN';
```

6. Replace `YOUR_MAPBOX_PUBLIC_TOKEN` with your own Mapbox public access token.

> **Important:** The repository does not include the author's Mapbox access token. Each user should provide their own public Mapbox token.

For public deployments, it is recommended to restrict the token to the domain where the application is hosted.

## How to Run

### Build the Docker image

```bash
docker build -t cascais-digital-twin .
```

### Run the Docker container

```bash
docker run -it --rm -p 8080:8080 -p 8501:8501 -p 8502:8502 cascais-digital-twin
```

## Access the App

After the Docker container starts, open the following URLs in your browser:

- **3D Map Dashboard:** [http://localhost:8080/test11.html](http://localhost:8080/test11.html)
- **Waste Forecast (Streamlit):** [http://localhost:8501](http://localhost:8501)
- **Waste Simulation (Streamlit):** [http://localhost:8502](http://localhost:8502)

## Main Components

### 3D Map Dashboard

The Mapbox-based dashboard visualizes waste-bin locations in Cascais and allows users to switch between the current bin status and short-term forecast GeoJSON files.

### Waste Forecast

The Streamlit forecasting module provides waste-fill forecasting and scenario visualization.

### Waste Collection Simulation

The municipal waste module supports:

- Climate-aware waste-fill forecasting
- Collection-threshold selection
- Truck-capacity estimation
- Single or dual fleet configuration
- Diesel and electric truck comparison
- CO₂ estimation
- Cluster-based route planning
- **Traveling Salesman Problem (TSP)-based route optimization**

### Textile Simulation

The textile module supports:

- Manual textile-bin placement
- Climate and tourism factors
- Textile fill-level simulation
- Recycling estimates
- CO₂ savings estimates
- Optional collection routing
- TSP-based collection route optimization

## Routing and TSP Optimization

The collection-routing component combines road-network analysis with the **Traveling Salesman Problem (TSP)** to determine an efficient visiting order for selected waste bins.

The project uses:

- **OpenStreetMap / OSMnx** to obtain the Cascais road network
- **NetworkX** to calculate shortest-path road distances between locations
- **Google OR-Tools** to solve the Traveling Salesman Problem (TSP) for route optimization

For each collection cluster, the depot and selected waste-bin locations are represented in a distance matrix. Google OR-Tools determines an optimized order in which the collection vehicle visits the bins and returns to the depot.

Google OR-Tools runs locally and does **not** require a Google API key or Google Maps API.

## Data Files

The application expects GeoJSON files such as:

```text
bins_latest_fixed.geojson
bins_prediction_1h.geojson
bins_prediction_2h.geojson
bins_prediction_3h.geojson
bins_prediction_4h.geojson
bins_prediction_5h.geojson
bins_prediction_6h.geojson
```

Make sure these files are available in the expected project directory before running the 3D dashboard.

## Security Notes

Do not commit private credentials or secret keys to the repository.

Files such as the following should remain private if they contain secrets:

```text
.env
.streamlit/secrets.toml
secrets.toml
```

The Mapbox token used by the browser is a public token, but this repository uses a placeholder so that each user can configure their own token.

## Copyright and Use

Copyright © 2026 **DICE TSP / Institut Mines-Télécom (IMT)**.

This repository contains research software developed within **Institut Mines-Télécom (IMT) /DICE TSP** as part of research work carried out in the context of the **CLIMABOROUGH project**.

The source code is made publicly available for **research visibility, academic demonstration, and reproducibility purposes**.

Unless separate permission is provided by the copyright holder, no permission is granted to:

- redistribute the software,
- modify and redistribute derivative versions,
- sublicense the software,
- use the software for commercial purposes.

Any reuse beyond viewing, evaluating, or reproducing the research results should be subject to prior authorization from the relevant copyright holder.

All rights reserved.

## Data Usage

The datasets included in or used by this project may be subject to separate ownership, licensing, privacy, or redistribution conditions.

Before publishing, redistributing, or reusing the GeoJSON datasets or waste-bin location data, users are responsible for verifying that they have the appropriate rights and permissions.

Third-party libraries, APIs, map services, datasets, and software components, including **Mapbox, OpenStreetMap, OSMnx, NetworkX, and Google OR-Tools**, remain subject to their respective licenses and terms of use.
