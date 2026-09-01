if 'RUN_CONTEXT' in globals():  # Running on Peliqan
    RUN_ENV = 'peliqan'
else:  # Running outside of Peliqan
    RUN_ENV = 'local'
    from peliqan import Peliqan
    import streamlit as st
    import os
    api_key = os.getenv("PELIQAN_API_KEY")
    if not api_key:
        st.error("PELIQAN_API_KEY environment variable is not set.")
        st.stop()
    interface_id = os.getenv("PELIQAN_INTERFACE_ID", 0)
    pq = Peliqan(api_key)
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is not None:
            RUN_CONTEXT = "interactive"
    except Exception:
        RUN_CONTEXT = "background"

# pq and st are already available — no imports needed for those
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

# 1. Hardcoded monthly order data
data = {
    'month': pd.date_range(start='2023-01-01', periods=24, freq='MS'),
    'orders': [120, 135, 128, 150, 160, 155, 170, 180, 175, 190, 200, 210,
               215, 225, 230, 240, 245, 250, 260, 270, 265, 280, 290, 300]
}
df = pd.DataFrame(data)
df = df.sort_values('month').reset_index(drop=True)
df['time_index'] = np.arange(len(df))

X = df[['time_index']] # scikit needs a 2D table hence [['...']]
y = df['orders']

# 2. Chronological train/test split
split = int(len(df) * 0.6)
X_train, X_test = X[:split], X[split:]
y_train, y_test = y[:split], y[split:]

# 3. Fit
model = LinearRegression()
model.fit(X_train, y_train)

# 4. Evaluate on test window
y_pred_test = model.predict(X_test)
mae = mean_absolute_error(y_test, y_pred_test)
rmse = np.sqrt(mean_squared_error(y_test, y_pred_test))
r2 = r2_score(y_test, y_pred_test)

st.title("Monthly Order Forecast")
st.metric("MAE: predictions are off by n orders (on avg)", f"{mae:.2f}")
st.metric("RMSE: if RMSE >> MAE = atleast one outlier in prediction", f"{rmse:.2f}")
st.metric("R²: accuracy of prediction", f"{r2:.3f}")

# 5. Forecast future months
n_future = 6
future_idx = np.arange(len(df), len(df) + n_future).reshape(-1, 1)
future_pred = model.predict(future_idx)
future_dates = pd.date_range(start=df['month'].iloc[-1] + pd.DateOffset(months=1),
                              periods=n_future, freq='MS')

# 6. Build one combined DataFrame with separate columns per segment
# so the chart naturally shows history / test window / future as distinct lines
chart_df = pd.DataFrame(index=pd.concat([df['month'], pd.Series(future_dates)], ignore_index=True))
chart_df['month'] = chart_df.index
chart_df = chart_df.reset_index(drop=True)

chart_df['actual'] = np.nan
chart_df.loc[:len(df) - 1, 'actual'] = df['orders'].values

chart_df['test_predicted'] = np.nan
chart_df.loc[split:len(df) - 1, 'test_predicted'] = y_pred_test

chart_df['future_predicted'] = np.nan
chart_df.loc[len(df):, 'future_predicted'] = future_pred

# Bridge the gap so the future line connects to the last known point
chart_df.loc[len(df) - 1, 'future_predicted'] = df['orders'].iloc[-1]

chart_df = chart_df.set_index('month')

st.subheader("History / Test Window / Future Forecast")
st.line_chart(chart_df[['actual', 'test_predicted', 'future_predicted']])

st.caption(
    f"Training window: {df['month'].iloc[0].date()} → {df['month'].iloc[split-1].date()} | "
    f"Test window: {df['month'].iloc[split].date()} → {df['month'].iloc[-1].date()} | "
    f"Forecast: {future_dates[0].date()} → {future_dates[-1].date()}"
)

st.subheader("Forecast values")
st.dataframe(pd.DataFrame({'month': future_dates, 'predicted_orders': future_pred}))