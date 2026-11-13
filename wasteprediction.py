import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from statsmodels.tsa.holtwinters import ExponentialSmoothing


st.set_page_config(page_title="Cascais — Multi-Year Waste Forecast", layout="wide")

st.markdown("""
<style>
body, .stApp {
    background-color: #f9f9f9;
    color: #111;
    font-family: "Segoe UI", sans-serif;
}
h1, h2, h3, h4, h5 {
    color: #0056b3;
}
.stDataFrame, .stTable {
    background-color: #ffffff !important;
    color: #000000 !important;
}
.stDownloadButton button {
    background-color: #007bff !important;
    color: white !important;
    border-radius: 10px;
    border: none;
}
.stDownloadButton button:hover {
    background-color: #0056b3 !important;
}
.stSidebar {
    background-color: #e9eef7 !important;
    color: #000000 !important;
}
</style>
""", unsafe_allow_html=True)

# TITLE
st.title(" Cascais — Urban Waste Generation Forecast")


# Data loader
@st.cache_data
def load_city_series(path="Urban_Waste_Trend_Data.xlsx"):
    df = pd.read_excel(path)
    df.columns = [c.strip() for c in df.columns]
    df_long = df.melt(id_vars=["Month"], var_name="Year", value_name="Tonnage")
    df_long["Year"] = df_long["Year"].astype(int)

    month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                 "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    df_long["Month"] = df_long["Month"].astype(str).str[:3]
    df_long["Month_num"] = df_long["Month"].map(month_map)
    df_long["Date"] = pd.to_datetime(dict(year=df_long["Year"], month=df_long["Month_num"], day=1))
    df_long = df_long.sort_values("Date").dropna(subset=["Tonnage"])
    series = df_long.set_index("Date")["Tonnage"].asfreq("MS")
    return series

# FORECAST  (holt damped trend)
def forecast_city(series, months_ahead=36):
    """Holt’s Damped Trend model for small datasets (no seasonality)."""
    model = ExponentialSmoothing(
        series,
        trend="add",
        damped_trend=True,
        initialization_method="estimated"
    ).fit(optimized=True, use_brute=True, remove_bias=True)

    preds = model.forecast(months_ahead)
    preds = pd.Series(preds, index=pd.date_range(series.index[-1] + pd.offsets.MonthBegin(1), periods=months_ahead, freq="MS"))

    
    random_factor = np.random.normal(1.0, 0.03, months_ahead)
    preds *= random_factor

    
    lower = series.min() * 0.8
    upper = series.max() * 1.5
    preds = preds.clip(lower, upper)

    return preds, model

# Data and sidebar
series = load_city_series()
years_ahead = st.sidebar.slider("Years to forecast ahead", 1, 5, 3)
show_3d = st.sidebar.checkbox("Show 3D Surface View", value=True)
variation = st.sidebar.slider("Confidence variation (±%)", 5, 25, 10) / 100

# FORECAST (damped trend)
preds, model = forecast_city(series, months_ahead=years_ahead * 12)

# Confidence bands (± variation)
upper_band = preds * (1 + variation)
lower_band = preds * (1 - variation)

# Combine actual + forecast
combined = pd.concat([series, preds])
df_forecast = pd.DataFrame({
    "Date": combined.index,
    "Tonnage": combined.values,
    "Type": ["Actual" if d <= series.index.max() else "Forecast" for d in combined.index]
})


# 2D Graph

fig2d = go.Figure()

# Actual data
fig2d.add_trace(go.Scatter(
    x=series.index, y=series.values, mode="lines+markers",
    name="Actual", line=dict(color="#007bff", width=3)
))

# Confidence band
fig2d.add_trace(go.Scatter(
    x=list(upper_band.index) + list(lower_band.index[::-1]),
    y=list(upper_band.values) + list(lower_band.values[::-1]),
    fill='toself',
    fillcolor='rgba(255,127,14,0.2)',
    line=dict(color='rgba(255,255,255,0)'),
    hoverinfo="skip",
    showlegend=True,
    name=f"Forecast Range (±{int(variation*100)}%)"
))

# Forecast line
fig2d.add_trace(go.Scatter(
    x=preds.index, y=preds.values, mode="lines+markers",
    name="Forecast", line=dict(color="#ff7f0e", width=3, dash="dot")
))

fig2d.update_layout(
    title=f"Urban Waste Forecast — Next {years_ahead} Years",
    xaxis_title="Date",
    yaxis_title="Tonnage (tons)",
    template="plotly_white",
    height=600,
    margin=dict(l=0, r=0, t=60, b=0),
    legend=dict(x=0.02, y=0.98, bgcolor="rgba(255,255,255,0.6)")
)
st.plotly_chart(fig2d, use_container_width=True)

# 3D model

if show_3d:
    st.subheader("3D Monthly Forecast Surface")

    df_forecast["Year"] = df_forecast["Date"].dt.year
    df_forecast["Month"] = df_forecast["Date"].dt.strftime("%b")

    pivot = df_forecast.pivot(index="Month", columns="Year", values="Tonnage")
    X, Y = np.meshgrid(pivot.columns, range(1, len(pivot.index) + 1))
    Z = pivot.values

    fig3d = go.Figure(data=[
        go.Surface(
            x=X,
            y=Y,
            z=Z,
            colorscale="YlGnBu",
            showscale=True,
            contours={"z": {"show": True, "color": "gray"}},
            opacity=0.9
        )
    ])

    fig3d.update_layout(
        scene=dict(
            xaxis_title="Year",
            yaxis_title="Month",
            zaxis_title="Tonnage (tons)",
            yaxis=dict(
                tickvals=list(range(1, 13)),
                ticktext=list(pivot.index)
            )
        ),
        title=f"3D Urban Waste Forecast — Next {years_ahead} Years",
        height=700,
        paper_bgcolor="#f9f9f9",
        font=dict(color="black")
    )
    st.plotly_chart(fig3d, use_container_width=True)

st.subheader(" Forecast Data Table")
df_forecast_display = df_forecast.copy()
df_forecast_display["Year"] = df_forecast_display["Date"].dt.year
df_forecast_display["Month"] = df_forecast_display["Date"].dt.strftime("%b")
st.dataframe(df_forecast_display.round(2), use_container_width=True)

csv_data = df_forecast.to_csv(index=False).encode("utf-8")
st.download_button(
    label=" Download Forecast Data (CSV)",
    data=csv_data,
    file_name="Urban_Waste_MultiYear_Forecast.csv",
    mime="text/csv"
)

# Map

st.markdown("---")
st.markdown("""
<a href="http://localhost:8080/digitalTwinSite.html" target="_blank">
    <button style="
        background: linear-gradient(90deg, #007bff, #00bfff);
        color: white; border: none; border-radius: 8px;
        padding: 10px 18px; font-size: 14px;
        cursor: pointer; box-shadow: 0 0 8px rgba(0,0,0,0.3);">
         Back to 3D Map
    </button>
</a>
""", unsafe_allow_html=True)
