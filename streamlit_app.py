import streamlit as st
import requests
import pandas as pd
import time
import math

st.set_page_config(page_title="EVE Online Торговый Аналитик", layout="wide")
st.title("📊 Анализатор межрегионального арбитража Jita ➡️ Amarr")

# --- ЗАГРУЗКА И КЭШИРОВАНИЕ ГЛОБАЛЬНОЙ БАЗЫ EVE ONLINE ---
# Функция кэшируется на 24 часа, чтобы не скачивать файлы при каждом клике
@st.cache_data(ttl=86400, show_spinner="Загрузка глобальной базы рынка EVE Online...")
def load_eve_market_data():
    try:
        # Скачиваем официальные дампы рынка (в сжатом виде .bz2)
        groups_url = "https://www.fuzzwork.co.uk/dump/latest/invMarketGroups.csv.bz2"
        types_url = "https://www.fuzzwork.co.uk/dump/latest/invTypes.csv.bz2"
        
        groups_df = pd.read_csv(groups_url)
        types_df = pd.read_csv(types_url)
        
        # Оставляем только те предметы, которые реально продаются на рынке
        types_df = types_df[(types_df['published'] == 1) & (types_df['marketGroupID'].notna())]
        
        # Строим иерархию (Категория -> Подкатегория -> Предмет)
        group_dict = groups_df.set_index('marketGroupID').to_dict('index')
        
        def get_full_group_name(group_id):
            path = []
            current_id = group_id
            while pd.notna(current_id) and current_id in group_dict:
                path.append(str(group_dict[current_id].get('marketGroupName', '')))
                current_id = group_dict[current_id].get('parentGroupID')
                if pd.isna(current_id): break
            return " ➡️ ".join(path[::-1]) # Переворачиваем, чтобы было "Главная -> Подчиненная"
            
        groups_df['fullPath'] = groups_df['marketGroupID'].apply(get_full_group_name)
        
        # Объединяем предметы с их категориями
        market_items = types_df[['typeID', 'typeName', 'marketGroupID']].merge(
            groups_df[['marketGroupID', 'fullPath']], on='marketGroupID'
        )
        return market_items
    except Exception as e:
        st.error(f"Ошибка загрузки базы данных: {e}")
        return pd.DataFrame()

# Загружаем базу
global_market_df = load_eve_market_data()

# --- ИНТЕРФЕЙС БОКОВОЙ ПАНЕЛИ ---
st.sidebar.header("🗂️ Выбор товаров для анализа")

items_to_scan = {}

if not global_market_df.empty:
    # 1. Выбор по рыночным категориям
    all_categories = sorted(global_market_df['fullPath'].unique())
    selected_categories = st.sidebar.multiselect(
        "1. Выберите категории рынка (можно начать печатать):", 
        all_categories,
        help="Например: Ship Equipment ➡️ Shield ➡️ Shield Extenders"
    )
    
    # 2. Ручное добавление конкретных предметов (для удобства)
    manual_items_str = st.sidebar.text_input(
        "2. ИЛИ впишите конкретные товары (через запятую):",
        placeholder="Например: Tengu, PLEX, Mordu NET Resonator"
    )

    st.sidebar.markdown("---")
    st.sidebar.header("🎛️ Настройки фильтрации")

    min_roi = st.sidebar.number_input("Минимальная рентабельность (ROI), %", min_value=0.0, max_value=1000.0, value=15.0, step=1.0)
    min_volume = st.sidebar.number_input("Мин. продаж в день (Амарр)", min_value=0, max_value=10000, value=5, step=1)
    
    # Собираем список TypeID на основе выбранных категорий
    if selected_categories:
        filtered_by_cat = global_market_df[global_market_df['fullPath'].isin(selected_categories)]
        for _, row in filtered_by_cat.iterrows():
            items_to_scan[row['typeName']] = row['typeID']
            
    # Добавляем вручную вписанные товары
    if manual_items_str:
        manual_names = [name.strip().lower() for name in manual_items_str.split(',') if name.strip()]
        # Ищем совпадения в базе
        found_manual = global_market_df[global_market_df['typeName'].str.lower().isin(manual_names)]
        for _, row in found_manual.iterrows():
            items_to_scan[row['typeName']] = row['typeID']

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
    
    # Информационная панель и прогресс-бар
    est_time = math.ceil((total_items * 0.15) / 60) # Примерно 0.15 сек на предмет
    info_text = st.empty()
    info_text.info(f"В очереди {total_items} предметов. Примерное время сканирования: ~{est_time} мин.")
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
            
            results.append({
                "Товар": item_name,
                "Type ID": type_id,
                "Жита Покупка (Max Buy)": round(jita_buy, 2),
                "Амарр Продажа (Min Sell)": round(amarr_sell, 2),
                "Грязная прибыль (ISK)": round(gross_profit, 2),
                "Рентабельность (%)": round(roi, 1),
                "Прод. в Амарре (шт/день)": round(daily_vol_avg, 1)
            })
            
        time.sleep(0.05) # Пауза против блокировки от ESI
        
    info_text.empty()
    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(results)

# --- ГЛАВНАЯ КНОПКА ЗАПУСКА ---

if st.button("🚀 Запустить сканирование рынка"):
    if not items_to_scan:
        st.warning("⚠️ Пожалуйста, выберите хотя бы одну категорию или впишите предмет вручную!")
    else:
        # Защита от слишком большого запроса
        if len(items_to_scan) > 2000:
            st.error(f"Вы выбрали {len(items_to_scan)} предметов. ESI может заблокировать нас. Пожалуйста, сузьте поиск до 1500 предметов за один раз.")
        else:
            with st.spinner("Работаем с ESI API..."):
                df = scan_market(items_to_scan)
                
                if not df.empty:
                    filtered_df = df[
                        (df["Рентабельность (%)"] >= min_roi) & 
                        (df["Прод. в Амарре (шт/день)"] >= min_volume)
                    ]
                    filtered_df = filtered_df.sort_values(by="Рентабельность (%)", ascending=False)
                    
                    if not filtered_df.empty:
                        st.success(f"Анализ завершен! Найдено {len(filtered_df)} профитных позиций из {len(items_to_scan)} просканированных.")
                        st.dataframe(filtered_df, use_container_width=True)
                        
                        csv = filtered_df.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="📥 Скачать CSV файл для ИИ-анализа",
                            data=csv,
                            file_name="eve_pro_arbitrage.csv",
                            mime="text/csv",
                        )
                    else:
                        st.warning("Ни один предмет не прошел фильтры. Снизьте требования к ROI или объему продаж.")
                else:
                    st.error("Не удалось найти цены. Возможно, ошибка на серверах ESI.")
