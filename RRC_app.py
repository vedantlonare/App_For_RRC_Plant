
import streamlit as st
import sqlite3, pandas as pd, io, json
from datetime import datetime

DB_PATH = "rrc.db"

@st.cache_resource
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def query_df(q, params=()):
    conn = get_conn()
    df = pd.read_sql_query(q, conn, params=params)
    return df

def run_sql(q, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(q, params)
    conn.commit()
    return cur

st.set_page_config(page_title="RRC Ops - Streamlit Prototype", layout="wide")

st.title("RRC Operations Management - Prototype (Streamlit)")

# Simple role-based "login" selector for demo
with st.sidebar:
    st.header("User")
    users = query_df("SELECT id,name,role FROM users WHERE active=1")
    user_select = st.selectbox("Select User (demo)", users['name'] + " — " + users['role'])
    selected = users.iloc[user_select.index if hasattr(user_select,'index') else 0] if False else None
    # better approach:
    user_idx = st.selectbox("User", users.index, format_func=lambda i: f\"{users.loc[i,'name']} — {users.loc[i,'role']}\")
    current_user = users.loc[user_idx]
    st.write(f\"Logged in as: **{current_user['name']}** ({current_user['role']})\")

role = current_user['role']
user_id = int(current_user['id'])

# Helper UI components
def get_plants_for_admin():
    return query_df("SELECT p.*, ms.id as sheet_id, ms.year, ms.month, ms.locked FROM plants p LEFT JOIN monthly_sheets ms ON ms.plant_id = p.id AND ms.year=2025 AND ms.month=10 ORDER BY p.id")

def get_plants_for_manager(uid):
    return query_df("SELECT p.* FROM plants p JOIN plant_assignments pa ON pa.plant_id=p.id WHERE pa.manager_id=?", (uid,))

def show_kpi_panel(plant_row):
    st.subheader(f\"{plant_row['name']}\")
    st.markdown(f\"**Location:** {plant_row['location']}\")
    # show sample KPIs from transactions
    q = \"SELECT type, SUM(quantity) as qty, SUM(value) as val FROM transactions t JOIN monthly_sheets ms ON t.sheet_id=ms.id WHERE ms.plant_id=? AND ms.year=2025 AND ms.month=10 GROUP BY type\"
    df = query_df(q, (plant_row['id'],))
    st.table(df)

def export_transactions_csv(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode('utf-8')

# Admin Interface
if role == 'admin':
    st.header("Admin Dashboard")
    plants_df = get_plants_for_admin()
    col1, col2 = st.columns([2,1])
    with col1:
        st.subheader("Plants Overview (~8)")
        for _, row in plants_df.iterrows():
            st.markdown(f\"**{row['name']}** — {row['location']}  \nLocked: **{bool(row['locked'])}**\")
            if st.button(f\"Open {row['name']}\", key=f"open_{row['id']}"):
                st.session_state['open_plant'] = int(row['id'])
    with col2:
        st.subheader("Admin Actions")
        st.write(\"Manage monthly locks and review requests.\")
        # Lock/unlock UI
        plant_choice = st.selectbox(\"Select Plant to Lock/Unlock\", plants_df['name'])
        pid = plants_df[plants_df['name']==plant_choice].iloc[0]['id']
        sheet_row = query_df(\"SELECT * FROM monthly_sheets WHERE plant_id=? AND year=2025 AND month=10\", (pid,))
        if not sheet_row.empty:
            locked = bool(sheet_row.iloc[0]['locked'])
            if locked:
                st.warning(f\"Sheet (Oct 2025) is LOCKED. Locked at: {sheet_row.iloc[0]['locked_at']}\")
                if st.button(\"Unlock Sheet (Admin)\"):
                    run_sql(\"UPDATE monthly_sheets SET locked=0, locked_at=NULL WHERE id=?\", (sheet_row.iloc[0]['id'],))
                    run_sql(\"INSERT INTO activity_logs (user_id,action,payload) VALUES (?,?,?)\", (user_id,'unlock_sheet',json.dumps({'sheet':int(sheet_row.iloc[0]['id'])})))
                    st.success(\"Sheet unlocked.\")
            else:
                if st.button(\"Lock Sheet (Admin)\"):
                    now = datetime.now().isoformat()
                    run_sql(\"UPDATE monthly_sheets SET locked=1, locked_at=? WHERE id=?\", (now, sheet_row.iloc[0]['id']))
                    run_sql(\"INSERT INTO activity_logs (user_id,action,payload) VALUES (?,?,?)\", (user_id,'lock_sheet',json.dumps({'sheet':int(sheet_row.iloc[0]['id'])})))
                    st.success(\"Sheet locked.\")
        # Manage managers
        st.markdown(\"---\")
        st.subheader(\"Manage Plant Managers\")
        mgrs = query_df(\"SELECT id,name,email FROM users WHERE role='manager'\")
        st.table(mgrs)
        if st.button(\"Create Sample Manager (Demo)\"):
            run_sql(\"INSERT INTO users (name,email,role) VALUES (?,?,?)\", (f\"New Manager {datetime.now().strftime('%H%M%S')}\", f\"m{datetime.now().timestamp()}@rrc.com\", 'manager'))
            st.experimental_rerun()

    # If admin opened a plant
    if 'open_plant' in st.session_state:
        pid = st.session_state['open_plant']
        st.markdown(\"---\")
        st.subheader(f\"Plant Detail: {plants_df[plants_df['id']==pid].iloc[0]['name']}\")
        show_kpi_panel(plants_df[plants_df['id']==pid].iloc[0])
        # Transactions view
        tx_df = query_df(\"SELECT t.*, u.name as created_by_name FROM transactions t JOIN monthly_sheets ms ON t.sheet_id=ms.id LEFT JOIN users u ON t.created_by=u.id WHERE ms.plant_id=? ORDER BY t.date DESC\", (pid,))
        st.subheader(\"Transactions (all)\")
        st.dataframe(tx_df)
        if not tx_df.empty:
            csv = export_transactions_csv(tx_df)
            st.download_button(\"Export Transactions CSV\", csv, file_name=f\"plant_{pid}_transactions.csv\")

# Manager Interface
elif role == 'manager':
    st.header("Plant Manager Dashboard")
    plants = get_plants_for_manager(user_id)
    if plants.empty:
        st.info("No plants assigned. Contact Admin.")
    else:
        chosen = st.selectbox("Select your plant", plants['name'])
        plant_row = plants[plants['name']==chosen].iloc[0]
        st.subheader(f\"{plant_row['name']} — {plant_row['location']}\")
        # show live progress (from transactions sum)
        q = \"SELECT SUM(CASE WHEN type='sale' THEN quantity ELSE 0 END) as sold, SUM(CASE WHEN type='purchase' THEN quantity ELSE 0 END) as purchased FROM transactions t JOIN monthly_sheets ms ON t.sheet_id=ms.id WHERE ms.plant_id=? AND ms.year=2025 AND ms.month=10\"
        sums = query_df(q, (plant_row['id'],))
        sold = float(sums['sold'].iloc[0] or 0)
        purchased = float(sums['purchased'].iloc[0] or 0)
        st.metric(\"Purchased (Oct 2025)\", purchased)
        st.metric(\"Sold (Oct 2025)\", sold)
        # show monthly target / margin input (demo)
        target = st.number_input(\"Monthly Target (units)\", min_value=0, value=200)
        progress = min(100, int((sold/target)*100) if target>0 else 0)
        st.progress(progress)
        st.write(f\"{sold}/{target} ({progress}%)\")
        # Transactions table and add form if sheet unlocked
        sheet_row = query_df(\"SELECT * FROM monthly_sheets WHERE plant_id=? AND year=2025 AND month=10\", (plant_row['id'],))
        if not sheet_row.empty:
            locked = bool(sheet_row.iloc[0]['locked'])
            if locked:
                st.warning(\"Monthly sheet is LOCKED. You cannot add or edit transactions.\")
                if st.button(\"Request Unlock (send request to Admin)\"):
                    run_sql(\"INSERT INTO requests (requester_id,plant_id,sheet_id,request_type,details) VALUES (?,?,?,?,?)\", (user_id, plant_row['id'], int(sheet_row.iloc[0]['id']), 'unlock_sheet', 'Please unlock for correction'))
                    st.success(\"Unlock request sent.\")
            else:
                st.info(\"Monthly sheet is OPEN for edits.\")
                with st.form(\"add_tx_form\"):
                    d = st.date_input(\"Date\", value=pd.to_datetime('2025-10-15'))
                    ttype = st.selectbox(\"Type\", ['purchase','sale','adjustment'])
                    item = st.text_input(\"Item\", value=\"Plastic Granules\")
                    qty = st.number_input(\"Quantity\", min_value=0.0, value=10.0)
                    val = st.number_input(\"Value\", min_value=0.0, value=100.0)
                    notes = st.text_area(\"Notes\")
                    submitted = st.form_submit_button(\"Add Transaction\")
                    if submitted:
                        run_sql(\"INSERT INTO transactions (sheet_id,date,type,item,quantity,value,created_by,notes) VALUES (?,?,?,?,?,?,?,?)\", (int(sheet_row.iloc[0]['id']), d.isoformat(), ttype, item, qty, val, user_id, notes))
                        run_sql(\"INSERT INTO activity_logs (user_id,action,payload) VALUES (?,?,?)\", (user_id,'add_tx',json.dumps({'plant':int(plant_row['id']), 'type':ttype, 'qty':qty})))
                        st.success(\"Transaction added.\")
        # Show transaction history
        tx_df = query_df(\"SELECT t.*, u.name as created_by_name FROM transactions t JOIN monthly_sheets ms ON t.sheet_id=ms.id LEFT JOIN users u ON t.created_by=u.id WHERE ms.plant_id=? ORDER BY t.date DESC\", (plant_row['id'],))
        st.subheader(\"Transaction History (all months)\")
        st.dataframe(tx_df)
        if not tx_df.empty:
            csv = export_transactions_csv(tx_df)
            st.download_button(\"Export Transactions CSV\", csv, file_name=f\"plant_{plant_row['id']}_transactions.csv\")

# Shared: activity logs (for demo show to all)
st.sidebar.markdown(\"---\")
if st.sidebar.button(\"Show Activity Logs (demo)\"):
    logs = query_df(\"SELECT al.*, u.name as user_name FROM activity_logs al LEFT JOIN users u ON al.user_id = u.id ORDER BY al.created_at DESC LIMIT 200\")
    st.sidebar.dataframe(logs)

st.sidebar.markdown(\"\\n---\\nRRC Prototype - Streamlit\")
