import io
import os
from flask import Flask, request, jsonify, render_template, g, send_file
import pandas as pd
import requests
from rag_db import RAGDatabase
from prometheus_flask_exporter import PrometheusMetrics
from prometheus_client import Histogram
import time
import json
import re
from monitoring import *

#testing mail
# Используем новую JSON-based систему шаблонов (v2.0)
from letters_templates_v2 import template_manager

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-unsafe-secret-key-change-me")
MODEL_API_URL = "http://localhost:5050/generate"

# Prometheus
CUSTOM_BUCKETS = [5, 10, 20, 30, 40, 50, 60]
metrics = PrometheusMetrics(app)
metrics.info("app_info", "O2 Copilot Flask App", version="1.0.0")

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Request duration in seconds (custom buckets, only /api/messages)",
    ["method", "path"],
    buckets=CUSTOM_BUCKETS,
)


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def before_request():
    request.start_time = time.time()
    if request.path.startswith("/api"):
        get_or_create_session_id()


@app.after_request
def after_request(response):
    if hasattr(request, "start_time"):
        duration = time.time() - request.start_time
        try:
            REQUEST_DURATION.labels(method=request.method, path=request.path).observe(
                duration
            )
        except ValueError as e:
            app.logger.error(f"Prometheus labeling error: {e}")
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/messages", methods=["POST"])
def messages():
    sid = get_or_create_session_id()
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "Invalid input"}), 400

    user_message = data["text"]
    db = get_db()
    db.execute(
        """
        INSERT INTO messages (session_id, timestamp, query_text)
        VALUES (?, ?, ?)
        """,
        (sid, datetime.now(tz=timezone(timedelta(hours=3))), user_message),
    )
    db.commit()

    selected_datasets = data.get("datasets", [])
    mode = data["mode"]
    print(
        f"Получено сообщение: {user_message}\nВыбранные датасеты: {selected_datasets}."
    )

    try:
        # Поиск в RAG-базе
        relevant_docs, user_message = rag_db.search(
            user_message, selected_datasets=selected_datasets, final_k=3, initial_k=25
        )
        print("Документы нашлись!")
        context = "\n\n".join(
            f"Metadata: {doc['metadata'].split('/')[-1]}\nData: {doc['chunk']}"
            for doc in relevant_docs
        )

        # Формирование системного промпта
        system_content = (
            "Ты — интеллектуальный корпоративный ассистент Игорь Иванович для сотрудников компании Nestle. Отвечай строго в формате HTML на основе предоставленного контекста. Не придумывай ничего от себя.\n"
            "=== ПОВЕДЕНЧЕСКИЕ ПРАВИЛА ===\n"
            "1. Используй ТОЛЬКО информацию из контекста. Если информации недостаточно — начни с: 'Информация по [объект] не найдена. Однако есть информация о...'\n"
            "2. Чётко разделяй сущности: потребители ≠ клиенты, клиенты ≠ поставщики, кофе ≠ детское питание и т.д.\n"
            "3. Старайся давать полный ответ.\n"
            "4. При наличии противоречий в источниках — перечисли ВСЕ варианты.\n"
            "5. Каждый смысловой фрагмент ответа сопровождай ссылкой на источник в формате [1], [2], ...\n"
            "6. В конце оцени релевантность контекста по 10-балльной шкале (0 -- контекст не предоставляет никакой информации для правильного ответа на вопрос, 10 -- в контексте есть вся информация для ответа на вопрос)\n\n"
            "=== ФОРМАТ HTML ===\n"
            "1. <h3> — заголовки\n"
            "2. <b>, <i> — выделение\n"
            "3. <ol>/<ul> — списки\n"
            "4. После двоеточий — строчная буква\n\n"
            "=== ОБЯЗАТЕЛЬНАЯ СТРУКТУРА ОТВЕТА ===\n"
            "1. Основной ответ\n"
            "2. Источники со списком документов (используй только те источники из контекста, перед которыми есть 'Metadata:')\n\n"
            "=== ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА ===\n\n"
            "<h3>Решение вопроса</h3>\n"
            "<p>Для решения проблемы необходимо выполнить следующие действия:</p>\n"
            "<ol>\n"
            "  <li>Первое действие [1]</li>\n"
            "  <li>Второе действие [2]</li>\n"
            "</ol>\n"
            "<h3>Источники</h3>\n"
            "<ol>\n"
            "  <li>1.docx</li>\n"
            "  <li>2.pdf</li>\n"
            "</ol>\n"
            "<p style='text-align: right;'><i>Точность ответа:<b> x/10</b></i></p>\n\n"
        )

        if mode == "letter":
            print(f"\n📧 Режим письма активирован")
            print(f"Запрос пользователя: {user_message[:100]}...")
            
            # ===== ПОЛУЧАЕМ actual_docs и links_dict ЗАРАНЕЕ =====
            try:
                with open("links.json", "r", encoding="utf-8") as f:
                    links_dict = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                links_dict = {}
            
            actual_docs = [
                doc["metadata"].split("docs_Samara/")[1] for doc in relevant_docs
            ]
            
            # ===== ПОИСК ШАБЛОНА =====
            template_config = template_manager.find_matching_template(
                query=user_message,
                error_message=context
            )
            
            if template_config:
                # ✅ Шаблон найден - используем его
                print(f"✉️ Найден шаблон: {template_config['description']}")
                print(f"⚙️ Действие: {template_config['action']}")
                print(f"📄 MSG файл: {template_config.get('msg_filename', 'нет')}")
                
                response_data = template_manager.prepare_letter_response(
                    template_config=template_config,
                    user_context=user_message
                )
                
                if response_data:
                    # Формируем mailto данные для кнопки
                    mailto_data = {
                        "to": response_data.get("to", "Customer.Service@nestle.ru"),
                        "cc": response_data.get("cc", ""),
                        "subject": response_data.get("subject", ""),
                        "body": response_data.get("response", "")
                    }
                    
                    # Формируем текст ответа с информацией о шаблоне
                    action = template_config['action']
                    action_text = template_config.get('action_text', '')
                    
                    # Добавляем предупреждение о действии
                    action_info = ""
                    if action == 'block_and_notify':
                        action_info = "<p><strong>⚠️ ДЕЙСТВИЕ:</strong> Блокировать IDoc и оповестить CSA</p>"
                    elif action == 'block_no_notify':
                        action_info = "<p><strong>⚠️ ДЕЙСТВИЕ:</strong> Блокировать IDoc БЕЗ оповещения CSA</p>"
                    elif action == 'push_and_notify':
                        action_info = "<p><strong>✅ ДЕЙСТВИЕ:</strong> Протолкнуть IDoc и оповестить</p>"
                    elif action == 'lenta_gln_change':
                        action_info = "<p><strong>🏪 ДЕЙСТВИЕ:</strong> Замена GLN для Ленты</p>"
                    
                    # Формируем HTML ответ
                    response_text = f"""<h3>Решение найдено</h3>
        <p><strong>Используется шаблон:</strong> {template_config['description']}</p>
        {action_info}
        <hr>
        <h4>Инструкция:</h4>
        <div style="white-space: pre-wrap;">{action_text}</div>"""
                    
                    # Обрабатываем источники
                    processed_response = process_sources(response_text, actual_docs, links_dict)
                    
                    return jsonify({
                        "type": "message",
                        "text": processed_response,
                        "mailto": mailto_data
                    })
            
            # ===== FALLBACK: AI ГЕНЕРАЦИЯ =====
            print("🤖 Шаблон не найден, генерируем письмо через AI...")
            
            system_content += (
                "\n\n=== РЕЖИМ НАПИСАНИЯ ПИСЬМА ===\n"
                "Пользователь хочет, чтобы ты составил официальное письмо.\n"
                "В конце ответа добавь JSON-блок. Например, такой:\n"
                "```json\n"
                "{\n"
                '  "mailto": {\n'
                '    "to": "customer.distributors@nestle.ru, Customer.Service@nestle.ru",\n'
                '    "cc": "Orders@nestle.ru",\n'
                '    "subject": "Тема письма",\n'
                '    "body": "Добрый день.\\nКоллеги, айдок блокирован.\\n\\nDemand Capture specialist\\nOrder to Cash\\nSBS Samara\\n\\nOrders@nestle.ru"\n'
                "  }\n"
                "}\n"
                "```\n"
                "Этот блок должен быть в самом конце ответа."
            )

            system_content += (
                "=== КОНТЕКСТ (для использования ниже) ===\n"
                f"{rag_db.last_glossary}"
                f"{context}"
            )

            # Отправка запроса
            response = requests.post(
                MODEL_API_URL,
                json={
                    "system_prompt": system_content,
                    "user_prompt": user_message,
                    "max_length": 1024,
                    "temperature": 0.15,
                    "top_p": 0.15,
                },
                timeout=60,
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()

            # Постобработка
            model_response = response.json()["response"]

            # Парсинг mailto из AI ответа
            match = re.search(
                r"```json\s*(\{[\s\S]*?\})\s*```", model_response, re.IGNORECASE
            )
            if not match:
                match = re.search(r"```\s*(\{[\s\S]*?\})\s*```", model_response)
            if not match:
                mailto_matches = list(
                    re.finditer(r'\{[^}]*"mailto"[^}]*\{[^}]*\}[^}]*\}', model_response)
                )
                if mailto_matches:
                    potential_json = mailto_matches[-1].group(0)
                    try:
                        parsed_test = json.loads(potential_json)
                        if "mailto" in parsed_test:
                            match = type(
                                "obj",
                                (object,),
                                {
                                    "group": lambda x: (
                                        potential_json if x == 1 else potential_json
                                    )
                                },
                            )
                    except:
                        pass

            mailto_data = None
            if match:
                try:
                    parsed = json.loads(match.group(1), strict=False)
                    mailto_data = parsed.get("mailto")
                    print(mailto_data)
                    model_response = model_response.replace(match.group(0), "").strip()
                except Exception as e:
                    print(f"Ошибка парсинга JSON для mailto: {e}")

            print(model_response)
            processed_response = process_sources(model_response, actual_docs, links_dict)

            return jsonify({
                "type": "message",
                "text": processed_response,
                "mailto": mailto_data
            })
        
        else:
            # ===== ОБЫЧНЫЙ РЕЖИМ (не письмо) =====
            print(f"\n💬 Обычный режим активирован")
            
            # Подготовка данных для обработки источников
            try:
                with open("links.json", "r", encoding="utf-8") as f:
                    links_dict = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                links_dict = {}
            
            actual_docs = [
                doc["metadata"].split("docs_Samara/")[1] for doc in relevant_docs
            ]
            
            # Добавляем контекст в системный промпт
            system_content += (
                "=== КОНТЕКСТ (для использования ниже) ===\n"
                f"{rag_db.last_glossary}"
                f"{context}"
            )
            
            # Отправка запроса к AI модели
            response = requests.post(
                MODEL_API_URL,
                json={
                    "system_prompt": system_content,
                    "user_prompt": user_message,
                    "max_length": 1024,
                    "temperature": 0.15,
                    "top_p": 0.15,
                },
                timeout=60,
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            
            # Постобработка ответа
            model_response = response.json()["response"]
            print(f"Ответ от модели получен: {model_response[:100]}...")
            
            # Обработка источников
            processed_response = process_sources(model_response, actual_docs, links_dict)
            
            return jsonify({
                "type": "message",
                "text": processed_response
            })

    except requests.exceptions.RequestException as e:
        print(f"Ошибка запроса к модели: {e}")
        return jsonify({"error": "Model service unavailable"}), 503
    except Exception as e:
        print(f"Ошибка: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/stats", methods=["GET"])
def stats_page():
    return render_template("stats.html")


@app.route("/stats/data", methods=["GET"])
def stats_data():
    return jsonify(build_stats_payload())


@app.route("/stats.xlsx", methods=["GET"])
def stats_excel():
    db = get_db()

    sessions_df = pd.read_sql_query("SELECT * FROM sessions", db)
    messages_df = pd.read_sql_query("SELECT * FROM messages", db)

    now = datetime.now(tz=timezone(timedelta(hours=3)))
    agg_rows = []

    for label, delta in [
        ("day", timedelta(days=1)),
        ("week", timedelta(days=7)),
        ("month", timedelta(days=30)),
        ("all_time", None),
    ]:
        if delta:
            dt_from = now - delta
        else:
            dt_from = datetime(1970, 1, 1, tzinfo=now.tzinfo)

        req_cnt = db.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (dt_from,)
        ).fetchone()[0]

        sess_cnt = db.execute(
            "SELECT COUNT(*) FROM sessions WHERE start_time >= ?", (dt_from,)
        ).fetchone()[0]

        agg_rows.append({"period": label, "requests": req_cnt, "sessions": sess_cnt})

    agg_df = pd.DataFrame(agg_rows)
    mps_df = pd.read_sql_query(
        """
        SELECT s.session_id, COUNT(m.id) AS messages_count
        FROM sessions s
        LEFT JOIN messages m ON s.session_id = m.session_id
        GROUP BY s.session_id
        """,
        db,
    )

    percent_df = pd.DataFrame(
        [{"percent_sessions_with_messages": percent_sessions_with_messages()}]
    )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        agg_df.to_excel(writer, sheet_name="aggregates", index=False)
        sessions_df.to_excel(writer, sheet_name="sessions", index=False)
        messages_df.to_excel(writer, sheet_name="messages", index=False)
        mps_df.to_excel(writer, sheet_name="messages_per_session", index=False)
        percent_df.to_excel(writer, sheet_name="percent", index=False)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="stats.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def process_sources(response_text, actual_docs, links_dict):
    """
    Обрабатывает ответ модели: проверяет источники и добавляет ссылки.
    Если источников нет или они вымышлены — ставит 0/10 и удаляет [1], [2] и т.д.
    """
    # Извлекаем раздел "Источники"
    sources_match = re.search(
        r"<h3>Источники</h3>\s*<ol>(.*?)</ol>", response_text, re.DOTALL
    )

    if not sources_match:
        # Нет раздела "Источники" — считаем, что их нет
        response_text = re.sub(r"\[\d+\]", "", response_text)
        response_text = re.sub(
            r"Точность ответа:<b> \d+/10</b>",
            "Точность ответа:<b> 0/10</b>",
            response_text,
        )
        new_sources_section = (
            '<h3>Источники</h3>\n<p style="color: red;">Не найдено!</p>'
        )
        response_text = re.sub(
            r"<h3>Источники</h3>\s*<ol>.*?</ol>",
            new_sources_section,
            response_text,
            flags=re.DOTALL,
        )
        return response_text

    sources_content = sources_match.group(1)
    source_items = re.findall(r"<li>(.*?)</li>", sources_content, re.DOTALL)

    # Фильтруем только те, что есть в links_dict
    valid_sources = []
    print(source_items)
    for item in source_items:
        clean_item = re.sub(r"<[^>]+>", "", item).strip()
        # Проверяем, соответствует ли хотя бы один реальный документ части текста
        matched = False
        for path in actual_docs:
            if (
                path in clean_item
                or clean_item in path
                or clean_item == path.split("/")[-1]
            ):
                if path in links_dict:
                    valid_sources.append((item, links_dict[path]))
                    matched = True
                    break
        if not matched:
            valid_sources.append((item, None))

    # Если ни один источник не валидный
    if not any(link is not None for _, link in valid_sources):
        response_text = re.sub(r"\[\d+\]", "", response_text)
        response_text = re.sub(
            r"Точность ответа:<b> \d+/10</b>",
            "Точность ответа:<b> 0/10</b>",
            response_text,
        )
        new_sources_section = (
            '<h3>Источники</h3>\n<p style="color: red;">Не найдено!</p>'
        )
    else:
        # Есть валидные источники — оборачиваем их в ссылки
        new_list_items = []
        for item, link in valid_sources:
            if link is not None:
                new_list_items.append(
                    f'<li><a href="{link}" target="_blank">{item}</a></li>'
                )
            else:
                new_list_items.append(f"<li>{item}</li>")
        new_sources_section = f"<h3>Источники</h3><ol>{''.join(new_list_items)}</ol>"

    # Заменяем весь блок источников
    response_text = re.sub(
        r"<h3>Источники</h3>\s*<ol>.*?</ol>",
        new_sources_section,
        response_text,
        flags=re.DOTALL,
    )

    return response_text


if __name__ == "__main__":
    rag_db = RAGDatabase()
    try:
        rag_db.load_index()
    except Exception as e:
        print(f"[!] Ошибка загрузки индекса: {e}")
        # print("[*] Строим новый индекс...")
        # rag_db.build_index()

    app.run(host="localhost", port=5000, debug=True, use_reloader=False)
