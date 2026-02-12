# -*- coding: utf-8 -*-
"""
Упрощенная система управления шаблонами писем на основе JSON конфигурации
Версия 2.0 - Легко редактируемая и расширяемая
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional
import extract_msg


class LetterTemplateManagerV2:
    """Менеджер шаблонов писем на основе JSON конфигурации"""
    
    def __init__(self, config_path: str = "templates/error_templates_config.json"):
        """
        Инициализация менеджера шаблонов
        
        Args:
            config_path: Путь к JSON файлу с конфигурацией
        """
        self.config_path = config_path
        self.templates = []
        self.actions = {}
        self.config = {}
        self.msg_folder = ""
        
        # Загружаем конфигурацию
        self._load_config()
        
        print(f"✅ Загружено {len(self.templates)} шаблонов из {config_path}")
    
    def _load_config(self):
        """Загружает конфигурацию из JSON файла"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.templates = data.get('templates', [])
            self.actions = data.get('actions', {})
            self.config = data.get('config', {})
            self.msg_folder = self.config.get('msg_folder', 'templates/errors')
            
            # Сортируем шаблоны по приоритету (от большего к меньшему)
            self.templates.sort(key=lambda x: x.get('priority', 0), reverse=True)
            
            print("=" * 80)
            print("КОНФИГУРАЦИЯ ЗАГРУЖЕНА")
            print("=" * 80)
            print(f"Шаблонов: {len(self.templates)}")
            print(f"Действий: {len(self.actions)}")
            print(f"MSG папка: {self.msg_folder}")
            print(f"Режим поиска: {self.config.get('search_mode', 'priority_weighted')}")
            print(f"Минимальный порог: {self.config.get('min_match_threshold', 0.6)}")
            print("=" * 80)
            
        except FileNotFoundError:
            print(f"❌ ОШИБКА: Файл конфигурации не найден: {self.config_path}")
            raise
        except json.JSONDecodeError as e:
            print(f"❌ ОШИБКА: Некорректный JSON в {self.config_path}: {e}")
            raise
    
    def reload_config(self):
        """Перезагружает конфигурацию из файла (для обновления без перезапуска)"""
        print("\n🔄 Перезагрузка конфигурации...")
        self._load_config()
        print("✅ Конфигурация обновлена")
    
    def _normalize_text(self, text: str) -> str:
        """
        Нормализует текст для поиска
        
        Args:
            text: Исходный текст
            
        Returns:
            Нормализованный текст
        """
        # Заменяем Unicode многоточие на обычное
        text = text.replace('…', '...')
        # Убираем лишние пробелы
        text = re.sub(r'\s+', ' ', text)
        # Убираем запятые
        text = text.replace(',', '')
        return text.strip()
    
    def find_matching_template(
        self, 
        query: str, 
        error_message: str = "", 
        debug: bool = False
    ) -> Optional[Dict]:
        """
        Находит подходящий шаблон по запросу
        
        Args:
            query: Запрос пользователя (ПРИОРИТЕТ)
            error_message: Текст ошибки из контекста (дополнительно)
            debug: Включить отладочный вывод
            
        Returns:
            dict с конфигом шаблона или None
        """
        # Нормализуем тексты
        normalized_query = self._normalize_text(query).lower()
        normalized_context = self._normalize_text(error_message).lower() if error_message else ""
        
        if debug:
            print(f"\n{'='*80}")
            print("🔍 ПОИСК ШАБЛОНА")
            print(f"{'='*80}")
            print(f"Запрос: {query[:150]}")
            print(f"Нормализованный запрос: {normalized_query[:150]}")
            if error_message:
                print(f"Контекст: {error_message[:150]}")
        
        best_match = None
        best_score = 0
        
        # Проходим по шаблонам (они уже отсортированы по приоритету)
        for template in self.templates:
            # Считаем сколько паттернов совпало
            patterns = template.get('patterns', [])
            alternative_patterns = template.get('alternative_patterns', [])
            
            # Основные паттерны (должны быть ВСЕ)
            main_match_count = 0
            main_in_query = 0
            
            for pattern in patterns:
                pattern_lower = pattern.lower()
                
                # ПРИОРИТЕТ: ищем в запросе
                if pattern_lower in normalized_query:
                    main_match_count += 1
                    main_in_query += 1
                # Дополнительно: ищем в контексте (но НЕ считаем как совпадение!)
                # elif pattern_lower in normalized_context:
                #     # Игнорируем паттерны найденные только в контексте
                #     pass
            
            # Альтернативные паттерны (достаточно одного набора)
            alt_match = False
            if alternative_patterns:
                for alt_group in alternative_patterns:
                    if all(alt.lower() in normalized_query for alt in alt_group):
                        alt_match = True
                        break
            
            # Проверяем совпадение
            main_patterns_count = len(patterns)
            has_alternatives = len(alternative_patterns) > 0
            
            if main_patterns_count > 0:
                match_ratio = main_match_count / main_patterns_count
            else:
                match_ratio = 0
            
            # Требуем чтобы ВСЕ паттерны были найдены В ЗАПРОСЕ
            # (или хотя бы альтернативные паттерны)
            is_match = (main_match_count == main_patterns_count) or alt_match
            
            if debug:
                print(f"\n📋 Шаблон: {template['name']}")
                print(f"   Приоритет: {template.get('priority', 0)}")
                print(f"   Паттернов: {main_patterns_count}")
                print(f"   Совпало: {main_match_count} (в запросе: {main_in_query})")
                print(f"   Совпадение: {match_ratio:.1%}")
                if is_match:
                    print(f"   ✅ СОВПАДЕНИЕ!")
            
            if is_match:
                # Вычисляем итоговый балл с учетом приоритета
                priority = template.get('priority', 0)
                score = match_ratio * 100 + priority
                
                if debug:
                    print(f"   💯 Балл: {score:.2f} (совпадение {match_ratio:.1%} + приоритет {priority})")
                
                # Обновляем лучшее совпадение
                if score > best_score:
                    best_score = score
                    best_match = template
        
        if best_match:
            if debug:
                print(f"\n{'='*80}")
                print(f"🎯 НАЙДЕН ЛУЧШИЙ ШАБЛОН")
                print(f"{'='*80}")
                print(f"ID: {best_match['id']}")
                print(f"Название: {best_match['name']}")
                print(f"Приоритет: {best_match.get('priority', 0)}")
                print(f"Балл: {best_score:.2f}")
                print(f"Действие: {best_match['action']}")
                print(f"MSG файл: {best_match['msg_file']}")
                print(f"{'='*80}\n")
            
            return {
                'id': best_match['id'],
                'description': best_match['description'],
                'action': best_match['action'],
                'msg_file': best_match['msg_file'],
                'msg_filename': best_match['msg_file'],
                'priority': best_match.get('priority', 0),
                'score': best_score,
                'comment': best_match.get('comment', '')
            }
        
        if debug:
            print(f"\n{'='*80}")
            print("❌ ШАБЛОН НЕ НАЙДЕН")
            print(f"{'='*80}\n")
        
        return None
    
    def prepare_letter_response(
        self,
        template_config: Dict,
        user_context: str = ""
    ) -> Optional[Dict]:
        """
        Подготавливает данные для письма на основе шаблона
        
        Args:
            template_config: Конфигурация найденного шаблона
            user_context: Контекст запроса пользователя
            
        Returns:
            dict с данными для письма или None
        """
        msg_filename = template_config.get('msg_file')
        if not msg_filename:
            print("❌ MSG файл не указан в шаблоне")
            return None
        
        # Полный путь к MSG файлу
        msg_path = os.path.join(self.msg_folder, msg_filename)
        
        if not os.path.exists(msg_path):
            print(f"❌ MSG файл не найден: {msg_path}")
            return None
        
        try:
            # Читаем MSG файл
            msg = extract_msg.Message(msg_path)
            
            # Извлекаем данные
            subject = msg.subject or ""
            body = msg.body or ""
            to = msg.to or "Customer.Service@nestle.ru"
            cc = msg.cc or ""
            
            # Получаем информацию о действии
            action = template_config.get('action', '')
            action_info = self.actions.get(action, {})
            
            return {
                "to": to,
                "cc": cc,
                "subject": subject,
                "response": body,
                "action": action,
                "action_text": action_info.get('display_name', ''),
                "notify_csa": action_info.get('notify_csa', True),
                "template_id": template_config.get('id', ''),
                "template_description": template_config.get('description', '')
            }
            
        except Exception as e:
            print(f"❌ Ошибка при чтении MSG файла {msg_path}: {e}")
            return None
    
    def add_template(
        self,
        template_id: str,
        name: str,
        description: str,
        patterns: List[str],
        action: str,
        msg_file: str,
        priority: int = 10,
        comment: str = ""
    ) -> bool:
        """
        Добавляет новый шаблон в конфигурацию
        
        Args:
            template_id: Уникальный ID шаблона
            name: Название шаблона
            description: Описание
            patterns: Список ключевых слов
            action: Действие (block_and_notify, push_and_notify, и т.д.)
            msg_file: Имя MSG файла
            priority: Приоритет (выше = важнее)
            comment: Комментарий
            
        Returns:
            True если успешно, False если ошибка
        """
        # Проверяем что такой ID еще нет
        if any(t['id'] == template_id for t in self.templates):
            print(f"❌ Шаблон с ID '{template_id}' уже существует!")
            return False
        
        # Создаем новый шаблон
        new_template = {
            "id": template_id,
            "name": name,
            "description": description,
            "patterns": patterns,
            "action": action,
            "msg_file": msg_file,
            "priority": priority,
            "comment": comment
        }
        
        # Добавляем в список
        self.templates.append(new_template)
        
        # Пересортировываем по приоритету
        self.templates.sort(key=lambda x: x.get('priority', 0), reverse=True)
        
        # Сохраняем в JSON
        return self._save_config()
    
    def _save_config(self) -> bool:
        """Сохраняет текущую конфигурацию в JSON файл"""
        try:
            data = {
                "templates": self.templates,
                "actions": self.actions,
                "config": self.config
            }
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ Конфигурация сохранена в {self.config_path}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка при сохранении конфигурации: {e}")
            return False
    
    def list_templates(self) -> List[Dict]:
        """Возвращает список всех шаблонов"""
        return self.templates
    
    def get_template_by_id(self, template_id: str) -> Optional[Dict]:
        """Возвращает шаблон по ID"""
        for template in self.templates:
            if template['id'] == template_id:
                return template
        return None


# Создаем глобальный экземпляр менеджера (для обратной совместимости)
try:
    template_manager = LetterTemplateManagerV2()
except Exception as e:
    print(f"⚠️  Не удалось загрузить конфигурацию шаблонов: {e}")
    template_manager = None


if __name__ == "__main__":
    # Тестирование
    print("\n" + "=" * 80)
    print("ТЕСТИРОВАНИЕ МЕНЕДЖЕРА ШАБЛОНОВ V2")
    print("=" * 80)
    
    if template_manager:
        # Тест 1
        print("\n📝 Тест 1: Duplicate PO RUEDIGIPER")
        result = template_manager.find_matching_template(
            "Duplicate PO (…) found for ship-to customer … (RUEDIGIPER)",
            debug=True
        )
        
        # Тест 2
        print("\n📝 Тест 2: RUEDIMAKSI RU3A-01")
        result = template_manager.find_matching_template(
            "Для клиента (RUEDIMAKSI) корректно размещение заказов RU3A-01 с указанием общего GLN в сегменте LF – 4607150089990",
            debug=True
        )
        
        # Тест 3
        print("\n📝 Тест 3: С префиксом 'Возникла ошибка'")
        result = template_manager.find_matching_template(
            "Возникла ошибка Duplicate PO (…) found for ship-to customer … (RUEDIGIPER)",
            error_message="Проверить GLN клиента в XD03. Дубликат заказа найден.",
            debug=True
        )
