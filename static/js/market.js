/* market.js — поведение кабинета без зависимостей.
 *
 * Правило: скрипт только дополняет разметку, ни один элемент не появляется
 * и не исчезает без него. Разметка читаема без JS.
 */
(function () {
  'use strict';

  /* ── Подтверждение деструктивных действий ─────────────────────────── */
  // Ручной confirm там, где в форме нет onsubmit (управление через fetch
  // и динамические формы): отправку не отменяем молча.
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var message = form.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  /* ── Счётчик символов в брифе ──────────────────────────────────────── */
  var brief = document.querySelector('textarea[name="brief"]');
  if (brief) {
    var counter = document.createElement('span');
    counter.className = 'field-hint';
    counter.setAttribute('aria-live', 'polite');
    var limit = 4000;
    var update = function () {
      var left = limit - brief.value.length;
      counter.textContent = 'Осталось символов: ' + Math.max(left, 0);
    };
    brief.insertAdjacentElement('afterend', counter);
    brief.addEventListener('input', update);
    update();
  }

  /* ── Имя выбранного файла ──────────────────────────────────────────── */
  Array.prototype.forEach.call(
    document.querySelectorAll('input[type="file"]'),
    function (input) {
      input.addEventListener('change', function () {
        var box = input.closest('.filebox');
        if (!box) return;
        var note = box.querySelector('.files__meta');
        if (!note) {
          note = document.createElement('div');
          note.className = 'files__meta';
          box.appendChild(note);
        }
        var names = Array.prototype.map.call(input.files, function (file) {
          return file.name;
        });
        note.textContent = names.length
          ? 'Выбрано: ' + names.join(', ')
          : '';
      });
    }
  );

  /* ── Отправка больших форм: блокировка кнопки от двойного клика ─────── */
  Array.prototype.forEach.call(
    document.querySelectorAll('form[enctype="multipart/form-data"]'),
    function (form) {
      form.addEventListener('submit', function () {
        var button = form.querySelector('button[type="submit"]');
        if (!button || button.dataset.locked === '1') return;
        button.dataset.locked = '1';
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        // Возврат кнопки, если браузер не ушёл со страницы (валидация HTML).
        window.setTimeout(function () {
          button.disabled = false;
          button.removeAttribute('aria-busy');
          delete button.dataset.locked;
        }, 8000);
      });
    }
  );
})();