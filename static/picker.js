// Injected into the sandboxed preview: hover to highlight, click to send a CSS selector to the parent page.
(function () {
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;pointer-events:none;border:2px solid #2f5bea;background:rgba(47,91,234,.15);z-index:2147483647';
  document.documentElement.appendChild(box);
  const okId = id => /^[A-Za-z][\w-]*$/.test(id);
  function selector(el) {
    if (el.id && okId(el.id)) return '#' + el.id;
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.body && el !== document.documentElement) {
      if (el.id && okId(el.id)) { parts.unshift('#' + el.id); return parts.join(' > '); }
      let part = el.tagName.toLowerCase();
      const same = Array.from(el.parentNode.children).filter(c => c.tagName === el.tagName);
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
      parts.unshift(part);
      el = el.parentElement;
    }
    return 'body > ' + parts.join(' > ');
  }
  document.addEventListener('mouseover', e => {
    const r = e.target.getBoundingClientRect();
    Object.assign(box.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
  }, true);
  document.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation();
    parent.postMessage({ cdSelector: selector(e.target) }, '*');
  }, true);
  document.addEventListener('submit', e => e.preventDefault(), true);
})();
