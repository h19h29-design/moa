/* Local input-loss warning only. Saving, authority and conflicts stay server-side. */
(() => {
  'use strict';
  const forms = [...document.querySelectorAll('form[data-queue-review]')];
  const value = e => (e.type === 'checkbox' || e.type === 'radio') ? e.checked : e.value;
  const originals = new Map(forms.map(form => [form,
    new Map([...form.elements].filter(e => e.name && e.type !== 'hidden').map(e => [e, value(e)]))]));
  const dirty = form => [...originals.get(form)].some(([e, initial]) => value(e) !== initial);
  const changed = () => forms.filter(dirty);
  const message = document.getElementById('queue-unsaved');
  let submitting = false;
  const update = () => { if (message) message.hidden = changed().length === 0; };
  document.addEventListener('input', update);
  document.addEventListener('change', update);
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href]');
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey || anchor.target === '_blank') return;
    const target = new URL(anchor.href, location.href);
    if (target.pathname === location.pathname && target.search === location.search && target.hash) return;
    if (changed().length && !window.confirm('저장하지 않은 입력이 있습니다. 입력을 버리고 이동할까요?')) {
      event.preventDefault();
    } else if (changed().length) {
      submitting = true; // The explicit discard confirmation already covered this navigation.
    }
  });
  for (const form of [...forms, ...document.querySelectorAll('form[data-queue-navigation]')]) form.addEventListener('submit', event => {
    if (changed().some(other => other !== form) &&
        !window.confirm(form.hasAttribute('data-queue-navigation') ?
          '저장하지 않은 입력이 있습니다. 입력을 버리고 목록·위치만 저장할까요?' :
          '다른 입력란에 저장하지 않은 수정이 있습니다. 이 항목만 저장하고 나머지 입력을 버릴까요?')) {
      event.preventDefault();
      return;
    }
    submitting = true;
  });
  window.addEventListener('beforeunload', event => {
    if (!submitting && changed().length) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pageshow', () => { submitting = false; update(); });
})();
