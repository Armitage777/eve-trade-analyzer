import streamlit as st
import requests
import pandas as pd
import time

# Настройка заголовков страницы
st.set_page_config(page_title="EVE Online Торговый Аналитик", layout="wide")
st.title("📊 Анализатор межрегионального арбитража Jita ➡️ Amarr")

# Базовый список предметов для теста (можно расширять)
ITEMS_DATABASE = {
    "Игры / Корабли / Расходники": {
        "Tengu": 29984,
        "PLEX": 44992,
        "Tritanium": 34,
        "Morphite": 11399
    },
    "NET Резонаторы": {
        "Sansha NET Resonator": 83469,
        "Mordu NET Resonator": 83470,
        "Serpentis NET Resonator": 83472
    }
}

# Боковая панель с настройками (Интерфейс)
st.sidebar.header("🎛️ Настройки фильтрации")

# Выбор категории предметов
categories = list(ITEMS_DATABASE.keys())
selected_category = st.sidebar.selectbox("Выберите категорию товаров:", categories)

# Ползунки для отсева неликвида и маржи
min_roi = st.sidebar.slider("Минимальная рентабельность (ROI), %", min_value=0, max_value=100, value=15)
min_volume = st.sidebar.slider("Минимальный объем продаж в Амарре (шт/день)", min_value=0, max_value=100, value=2)

# Функция для безопасного запроса к ESI API
def fetch_esi_data(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

# Функция сбора цен и истории
def scan_market(items):
    results = []
    
    for item_name, type_id in items.items():
        # 1. Получаем ордера в Жите (10000002)
        jita_orders = fetch_esi_data(f"https://esi.evetech.net/latest/markets/10000002/orders/?datasource=tranquility&order_type=all&type_id={type_id}")
        # 2. Получаем ордера в Амарре (10000043)
        amarr_orders = fetch_esi_data(f"https://esi.evetech.net/latest/markets/10000043/orders/?datasource=tranquility&order_type=all&type_id={type_id}")
        # 3. Получаем историю торгов в Амарре для расчета ликвидности
        amarr_history = fetch_esi_data(f"https://esi.evetech.net/latest/markets/10000043/history/?datasource=tranquility&type_id={type_id}")
        
        jita_buy = 0
        amarr_sell = 0
        daily_vol_avg = 0
        
        # Считаем лучшую скупку в Жите (Max Buy)
        if jita_orders:
            buy_prices = [o['price'] for o in jita_orders if o['is_buy_order']]
            if buy_prices: jita_buy = max(buy_prices)
            
        # Считаем лучшую продажу в Амарре (Min Sell)
        if amarr_orders:
            sell_prices = [o['price'] for o in amarr_orders if not o['is_buy_order']]
            if sell_prices: amarr_sell = min(sell_prices)
            
        # Считаем средний объем продаж в Амарре за последние 7 дней
        if amarr_history:
            last_days = amarr_history[-7:]
            if last_days:
                daily_vol_avg = sum([day['volume'] for day in last_days]) / len(last_days)
        
        # Математика профита
        if jita_buy > 0 and amarr_sell > 0:
            gross_profit = amarr_sell - jita_buy
            roi = (gross_profit / jita_buy) * 100
            
            results.append({
                "Товар": item_name,
                "Type ID": type_id,
                "Жита Покупка (Max Buy)": round(jita_buy, 2),
                "Амарр Продажа (Min Sell)": round(amarr_sell, 2),
                "Грязная прибыль (ISK)": round(gross_profit, 2),
                "Рентабельность (ROI %)": round(roi, 1),
                "Прод. в Амарре (ср. шт/день)": round(daily_vol_avg, 1)
            })
            
        time.sleep(0.1) # Легкая задержка, чтобы не спамить сервер CCP
        
    return pd.DataFrame(results)

# Кнопка запуска анализа
if st.button("🚀 Запустить сканирование рынка"):
    with st.spinner("Сбор данных из ESI API... Пожалуйста, подождите."):
        items_to_scan = ITEMS_DATABASE[selected_category]
        df = scan_market(items_to_scan)
        
        if not df.empty:
            # Применяем фильтры из боковой панели
            filtered_df = df[
                (df["Рентабельность (ROI %)"] >= min_roi) & 
                (df["Прод. в Амарре (ср. шт/день)"] >= min_volume)
            ]
            
            if not filtered_df.empty:
                st.success("Анализ успешно завершен!")
                
                # Вывод таблицы на экран
                st.dataframe(filtered_df, use_container_width=True)
                
                # Кнопка скачивания файла для загрузки в ИИ
                csv = filtered_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Скачать CSV файл для ИИ-анализа",
                    data=csv,
                    file_name="eve_market_analysis.csv",
                    mime="text/csv",
                )
            else:
                st.warning("Товары найдены, но ни один не подошел под ваши критерии фильтрации.")
        else:
            st.error("Не удалось получить данные. Проверьте статус серверов ESI.")
