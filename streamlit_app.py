import streamlit as st
import requests
import pandas as pd
import time
import math

st.set_page_config(page_title="EVE Online Торговый Аналитик", layout="wide")
st.title("📊 Анализатор межрегионального арбитража Jita ➡️ Amarr")

# --- ЗАГРУЗКА ЛОКАЛЬНОЙ БАЗЫ EVE ONLINE ---
@st.cache_data(show_spinner="Сборка дерева рынка из локальных файлов...")
def load_eve_market_data():
    try:
        groups_df = pd.read_csv('invMarketGroups.csv')
        types_df = pd.read_csv('invTypes.csv')
        
        # Оставляем только те предметы, которые реально существуют на рынке
        types_df = types_df[(types_df['published'] == 1) & (types_df['marketGroupID'].notna())]
        
        group_dict = groups_df.set_index('marketGroupID').to_dict('index')
        
        # Рекурсивная функция для построения полного пути категории
        def get_full_group_name(group_id):
            path = []
            current_id = group_id
            while pd.notna(current_id) and current_id in group_dict:
                path.append(str(group_dict[current_id].get('marketGroupName', '')))
                current_id = group_dict[current_id].get('parentGroupID')
                if pd.isna(current_id): break
            return " ➡️ ".join(path[::-1])
            
        groups_df['fullPath'] = groups_df['marketGroupID'].apply(get_full_group_name)
        
        # Выделяем корневую категорию для удобного каскадного фильтра
        groups_df['rootCategory'] = groups_df['fullPath'].apply(lambda x: x.split(" ➡️ ")[0] if isinstance(x, str) else "")
        
        market_items = types_df[['typeID', 'typeName', 'marketGroupID']].merge(
            groups_df[['marketGroupID', 'fullPath', 'rootCategory']], on='marketGroupID'
        )
        return market_items
    except FileNotFoundError:
        st.error("Файлы базы данных не найдены! Убедитесь, что invMarketGroups.csv и invTypes.csv лежат на GitHub в той же папке.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Ошибка чтения базы данных: {e}")
        return pd.DataFrame()

# Загружаем базу данных рынка
global_market_df = load_eve_market_data()

# --- ИНТЕРФЕЙС БОКОВОЙ ПАНЕЛИ ---
st.sidebar.header("🗂️ Выбор товаров для анализа")

items_to_scan = {}

if not global_market_df.empty:
    # 1. Выбор корневой группы (Дерево рынка)
    root_categories = ["Все категории"] + sorted(global_market_df['rootCategory'].unique().tolist())
    selected_root = st.sidebar.selectbox("1. Основная группа рынка:", root_categories)
    
    # Фильтруем доступные пути в зависимости от выбранной корневой группы
    if selected_root == "Все категории":
        available_paths = sorted(global_market_df['fullPath'].unique())
    else:
        available_paths = sorted(global_market_df[global_market_df['rootCategory'] == selected_root]['fullPath'].unique())
    
    # 2. Выбор конкретных подкатегорий
    selected_categories = st.sidebar.multiselect(
        "2. Конкретные подкатегории:", 
        available_paths,
        help="Выберите одну или несколько папок рынка для сканирования"
    )
    
    # 3. Ручной ввод отдельных позиций
    manual_items_str = st.sidebar.text_input(
        "3. ИЛИ впишите конкретные товары (через запятую):",
        placeholder="Например: Tengu, PLEX"
    )

    st.sidebar.markdown("---")
    st.sidebar.header("🎛️ Настройки фильтрации")

    min_roi = st.sidebar.number_input("Минимальная рентабельность (ROI), %", min_value=0.0, max_value=1000.0, value=15.0, step=1.0)
    min_volume = st.sidebar.number_input("Мин. продаж в день (Амарр, шт)", min_value=0, max_value=10000, value=5, step=1)
    
    # Новый фильтр по объёму ISK в день
    min_volume_isk = st.sidebar.number_input(
        "Мин. оборот в Амарре (ISK/день)", 
        min_value=0, 
        value=50000000, 
        step=10000000,
        help="Отсекает товары, у которых дневной оборот (кол-во * цену) ниже указанного"
    )
    
    # Сбор ID предметов на основе выбранных фильтров ДО нажатия кнопки
    if selected_categories:
        filtered_by_cat = global_market_df[global_market_df['fullPath'].isin(selected_categories)]
        for _, row in filtered_by_cat.iterrows():
            items_to_scan[row['typeName']] = row['typeID']
            
    if manual_items_str:
        manual_names = [name.strip().lower() for name in manual_items_str.split(',') if name.strip()]
        found_manual = global_market_df[global_market_df['typeName'].str.lower().isin(manual_names)]
        for _, row in found_manual.iterrows():
            items_to_scan[row['typeName']] = row['typeID']

    # --- ДИНАМИЧЕСКИЙ СЧЕТЧИК ОЧЕРЕДИ ДО ЗАПУСКА ---
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📊 Параметры текущей очереди")
    num_items = len(items_to_scan)
    est_time_min = math.ceil((num_items * 0.18) / 60) # ~0.18 сек на один цикл запросов к ESI
    
    col_num, col_time = st.sidebar.columns(2)
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
            daily_turnover_isk = daily_vol_avg * amarr_sell # Расчет денежного объема
            
            results.append({
                "Товар": item_name,
                "Type ID": type_id,
                "Жита Покупка (Max Buy)": round(jita_buy, 2),
                "Амарр Продажа (Min Sell)": round(amarr_sell, 2),
                "Грязная прибыль (ISK)": round(gross_profit, 2),
                "Рентабельность (%)": round(roi, 1),
                "Прод. в Амарре (шт/день)": round(daily_vol_avg, 1),
                "Оборот в Амарре (ISK/день)": round(daily_turnover_isk, 2)
            })
            
        time.sleep(0.04)
        
    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(results)

# --- ГЛАВНАЯ КНОПКА ЗАПУСКА ---
if st.button("🚀 Запустить сканирование рынка"):
    if not items_to_scan:
        st.warning("⚠️ Пожалуйста, выберите хотя бы одну категорию или впишите предмет вручную!")
    else:
        if len(items_to_scan) > 2500:
            st.error(f"Вы выбрали {len(items_to_scan)} предметов. ESI API может временно ограничить запросы. Пожалуйста, сузьте выборку до 2500 позиций.")
        else:
            with st.spinner("Сбор актуальных стаканов из ESI API..."):
                df = scan_market(items_to_scan)
                
                if not df.empty:
                    # Применяем все фильтры, включая новый фильтр по обороту ISK
                    filtered_df = df[
                        (df["Рентабельность (%)"] >= min_roi) & 
                        (df["Прод. в Амарре (шт/день)"] >= min_volume) &
                        (df["Оборот в Амарре (ISK/день)"] >= min_volume_isk)
                    ]
                    filtered_df = filtered_df.sort_values(by="Рентабельность (%)", ascending=False)
                    
                    if not filtered_df.empty:
                        st.success(f"Анализ завершен! Найдено {len(filtered_df)} выгодных позиций из {len(items_to_scan)} проверенных.")
                        
                        # Отображение таблицы с красивым разделением разрядов чисел (1,000,000)
                        st.dataframe(
                            filtered_df, 
                            use_container_width=True,
                            column_config={
                                "Жита Покупка (Max Buy)": st.column_config.NumberColumn(format="%,.2f ISK"),
                                "Амарр Продажа (Min Sell)": st.column_config.NumberColumn(format="%,.2f ISK"),
                                "Грязная прибыль (ISK)": st.column_config.NumberColumn(format="%,.2f ISK"),
                                "Оборот в Амарре (ISK/день)": st.column_config.NumberColumn(format="%,.0f ISK"),
                                "Рентабельность (%)": st.column_config.NumberColumn(format="%.1f %%"),
                                "Прод. в Амарре (шт/день)": st.column_config.NumberColumn(format="%.1f")
                            }
                        )
                        
                        csv = filtered_df.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="📥 Скачать CSV файл для ИИ-анализа",
                            data=csv,
                            file_name="eve_pro_arbitrage.csv",
                            mime="text/csv",
                        )
                    else:
                        st.warning("Ни один товар не подошел под выбранные критерии. Попробуйте снизить планку ROI или требуемого оборота в ISK.")
                else:
                    st.error("Не удалось получить данные о ценах. Проверьте соединение с ESI API.")
