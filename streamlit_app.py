import streamlit as st
import requests
import pandas as pd
import time
import math
from streamlit_tree_select import tree_select

st.set_page_config(page_title="EVE Online Торговый Аналитик", layout="wide")
st.title("📊 Анализатор межрегионального арбитража Jita ➡️ Amarr")

# --- ИНИЦИАЛИЗАЦИЯ ПАМЯТИ ПРИЛОЖЕНИЯ (SESSION STATE) ---
if "scan_results" not in st.session_state:
    st.session_state.scan_results = None

# --- ЗАГРУЗКА ЛОКАЛЬНОЙ БАЗЫ EVE ONLINE ---
@st.cache_data(show_spinner="Сборка интерактивного дерева рынка...")
def load_eve_market_data():
    try:
        groups_df = pd.read_csv('invMarketGroups.csv')
        types_df = pd.read_csv('invTypes.csv')
        
        types_df = types_df[(types_df['published'] == 1) & (types_df['marketGroupID'].notna())]
        group_dict = groups_df.set_index('marketGroupID').to_dict('index')
        
        def get_all_parent_groups(group_id):
            path = []
            current_id = group_id
            while pd.notna(current_id) and current_id in group_dict:
                path.append(int(current_id))
                current_id = group_dict[current_id].get('parentGroupID')
                if pd.isna(current_id): break
            return path
            
        types_df['all_groups'] = types_df['marketGroupID'].apply(get_all_parent_groups)
        
        published_group_ids = set()
        for groups_list in types_df['all_groups']:
            published_group_ids.update(groups_list)
            
        from collections import defaultdict
        children_map = defaultdict(list)
        roots = []
        
        for g_id in published_group_ids:
            parent = group_dict[g_id].get('parentGroupID')
            if pd.isna(parent) or parent not in published_group_ids:
                roots.append(g_id)
            else:
                children_map[int(parent)].append(g_id)
                
        def make_node(g_id):
            name = group_dict[g_id].get('marketGroupName', f"Группа {g_id}")
            node = {
                "label": str(name),
                "value": str(int(g_id)) 
            }
            children_ids = children_map.get(g_id, [])
            if children_ids:
                children_ids.sort(key=lambda x: str(group_dict[x].get('marketGroupName', '')))
                node["children"] = [make_node(c_id) for c_id in children_ids]
            return node
            
        roots.sort(key=lambda x: str(group_dict[x].get('marketGroupName', '')))
        tree_nodes = [make_node(r_id) for r_id in roots]
        
        return types_df[['typeID', 'typeName', 'marketGroupID', 'all_groups']], tree_nodes
    except FileNotFoundError:
        st.error("Файлы базы данных не найдены! Убедитесь, что invMarketGroups.csv и invTypes.csv лежат на GitHub.")
        return pd.DataFrame(), []
    except Exception as e:
        st.error(f"Ошибка чтения базы данных: {e}")
        return pd.DataFrame(), []

# Загружаем данные
global_market_df, market_tree_nodes = load_eve_market_data()

# --- СБОРКА БОКОВОЙ ПАНЕЛИ (ВСЕ ЭЛЕМЕНТЫ ТЕПЕРЬ ТУТ) ---
items_to_scan = {}

with st.sidebar:
    st.header("🗂️ Дерево рынка EVE Online")
    
    if market_tree_nodes:
        st.markdown("Отметьте нужные категории:")
        # Компонент дерева теперь вызывается строго внутри сайдбара
        return_select = tree_select(market_tree_nodes)
        selected_group_ids = return_select.get("checked", []) if return_select else []
        
        manual_items_str = st.sidebar.text_input(
            "ИЛИ впишите конкретные товары (через запятую):",
            placeholder="Например: Tengu, PLEX"
        )

        st.markdown("---")
        st.header("🎛️ Настройки фильтрации")

        min_roi = st.number_input("Минимальная рентабельность (ROI), %", min_value=0.0, max_value=1000.0, value=15.0, step=1.0)
        min_volume = st.number_input("Мин. продаж в день (Амарр, шт)", min_value=0, max_value=10000, value=5, step=1)
        
        min_volume_isk = st.number_input(
            "Мин. оборот в Амарре (день)", 
            min_value=0, 
            value=50000000, 
            step=10000000
        )
        # Динамическое красивое разделение разрядов для проверки ввода пользователем
        formatted_isk_label = f"{min_volume_isk:,.0f}".replace(",", " ")
        st.markdown(f"👉 *Текущий лимит оборота: **{formatted_isk_label}***")
        
        # Маппинг выбранных групп
        selected_group_set = set(int(x) for x in selected_group_ids)
        if selected_group_set:
            for _, row in global_market_df.iterrows():
                if any(g_id in selected_group_set for g_id in row['all_groups']):
                    items_to_scan[row['typeName']] = row['typeID']
                    
        if manual_items_str:
            manual_names = [name.strip().lower() for name in manual_items_str.split(',') if name.strip()]
            found_manual = global_market_df[global_market_df['typeName'].str.lower().isin(manual_names)]
            for _, row in found_manual.iterrows():
                items_to_scan[row['typeName']] = row['typeID']

        st.markdown("---")
        st.markdown("### 📊 Параметры текущей очереди")
        num_items = len(items_to_scan)
        est_time_min = math.ceil((num_items * 0.18) / 60)
        
        col_num, col_time = st.columns(2)
        col_num.metric("Товаров к скану", f"{num_items} шт.")
        col_time.metric("Время ожидания", f"~{est_time_min} мин")

# --- ЛОГИКА РАБОТЫ С ESI API ---
def fetch_esi_data(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200: return response.json()
    except: pass
    return None

def scan_market(items_dict):
    results = []
    total_items = len(items_dict)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, (item_name, type_id) in enumerate(items_dict.items()):
        progress_bar.progress((idx + 1) / total_items)
        status_text.text(f"Сканирую: {item_name} ({idx+1}/{total_items})")
        
        jita_orders = fetch_esi_data(f"https://esi.evetech.net/latest/markets/10000002/orders/?datasource=tranquility&order_type=all&type_id={type_id}")
        amarr_orders = fetch_esi_data(f"https://esi.evetech.net/latest/markets/10000043/orders/?datasource=tranquility&order_type=all&type_id={type_id}")
        amarr_history = fetch_esi_data(f"https://esi.evetech.net/latest/markets/10000043/history/?datasource=tranquility&type_id={type_id}")
        
        jita_buy, amarr_sell, daily_vol_avg = 0, 0, 0
        
        if jita_orders:
            buy_prices = [o['price'] for o in jita_orders if o['is_buy_order']]
            if buy_prices: jita_buy = max(buy_prices)
            
        if amarr_orders:
            sell_prices = [o['price'] for o in amarr_orders if not o['is_buy_order']]
            if sell_prices: amarr_sell = min(sell_prices)
            
        if amarr_history:
            last_days = amarr_history[-7:]
            if last_days:
                daily_vol_avg = sum([day['volume'] for day in last_days]) / len(last_days)
        
        if jita_buy > 0 and amarr_sell > 0:
            gross_profit = amarr_sell - jita_buy
            roi = (gross_profit / jita_buy) * 100
            daily_turnover_isk = daily_vol_avg * amarr_sell
            
            results.append({
                "Товар": item_name,
                "Type ID": type_id,
                "Жита Покупка (Max Buy)": round(jita_buy, 2),
                "Амарр Продажа (Min Sell)": round(amarr_sell, 2),
                "Грязная прибыль": round(gross_profit, 2),
                "Рентабельность (%)": round(roi, 1),
                "Прод. в Амарре (шт/день)": round(daily_vol_avg, 1),
                "Оборот в Амарре (день)": round(daily_turnover_isk, 2)
            })
            
        time.sleep(0.04)
        
    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(results)

# --- КНОПКА ЗАПУСКА С КЭШИРОВАНИЕМ ---
if st.button("🚀 Запустить сканирование рынка"):
    if not items_to_scan:
        st.warning("⚠️ Пожалуйста, выберите хотя бы одну категорию в дереве рынка!")
    else:
        if len(items_to_scan) > 2500:
            st.error(f"Вы выбрали слишком много позиций ({len(items_to_scan)}). Пожалуйста, сузьте выборку до 2500.")
        else:
            with st.spinner("Сбор актуальных стаканов из ESI API..."):
                # Сохраняем сырые результаты в стейт сессии
                st.session_state.scan_results = scan_market(items_to_scan)

# --- ОТРИСОВКА РЕЗУЛЬТАТОВ (РАБОТАЕТ ВСЕГДА, ЕСЛИ ЕСТЬ ДАННЫЕ В ПАМЯТИ) ---
if st.session_state.scan_results is not None:
    df = st.session_state.scan_results
    
    if not df.empty:
        # Живая фильтрация прямо на экране без перезапуска сканирования
        filtered_df = df[
            (df["Рентабельность (%)"] >= min_roi) & 
            (df["Прод. в Амарре (шт/день)"] >= min_volume) &
            (df["Оборот в Амарре (день)"] >= min_volume_isk)
        ]
        filtered_df = filtered_df.sort_values(by="Рентабельность (%)", ascending=False)
        
        if not filtered_df.empty:
            st.success(f"Отображено {len(filtered_df)} прибыльных позиций из {len(df)} в кэше.")
            
            # Отображение таблицы БЕЗ слова ISK
            st.dataframe(
                filtered_df, 
                use_container_width=True,
                column_config={
                    "Жита Покупка (Max Buy)": st.column_config.NumberColumn(format="%,.2f"),
                    "Амарр Продажа (Min Sell)": st.column_config.NumberColumn(format="%,.2f"),
                    "Грязная прибыль": st.column_config.NumberColumn(format="%,.2f"),
                    "Оборот в Амарре (день)": st.column_config.NumberColumn(format="%,.0f"),
                    "Рентабельность (%)": st.column_config.NumberColumn(format="%.1f %%"),
                    "Прод. в Амарре (шт/день)": st.column_config.NumberColumn(format="%.1f")
                }
            )
            
            csv = filtered_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Скачать текущую выборку (CSV)",
                data=csv,
                file_name="eve_pro_arbitrage.csv",
                mime="text/csv",
            )
        else:
            st.warning("В кэше есть данные, но ни один товар не соответствует текущим фильтрам ползунков. Снизьте требования.")
