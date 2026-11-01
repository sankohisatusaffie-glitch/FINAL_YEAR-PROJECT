(() => {
  document.querySelectorAll('[data-history-back]').forEach((button) => {
    button.addEventListener('click', () => {
      let cameFromThisSite = false;
      try {
        cameFromThisSite = Boolean(document.referrer) && new URL(document.referrer).origin === window.location.origin;
      } catch {
        cameFromThisSite = false;
      }
      if (window.history.length > 1 && cameFromThisSite) {
        window.history.back();
      } else {
        window.location.assign(button.dataset.homeUrl);
      }
    });
  });

  const menuButton = document.querySelector('.menu-toggle');
  const publicNav = document.querySelector('#main-nav');
  if (menuButton && publicNav) {
    menuButton.addEventListener('click', () => {
      const expanded = menuButton.getAttribute('aria-expanded') === 'true';
      menuButton.setAttribute('aria-expanded', String(!expanded));
      publicNav.classList.toggle('is-open', !expanded);
    });
  }

  const sidebar = document.querySelector('#portal-sidebar');
  const sidebarToggles = document.querySelectorAll('[data-sidebar-toggle]');
  const backdrop = document.querySelector('[data-sidebar-close]');
  if (sidebar && sidebarToggles.length && backdrop) {
    const mobileViewport = window.matchMedia('(max-width: 760px)');
    let lastToggle = sidebarToggles[0];
    sidebar.inert = mobileViewport.matches;
    const setOpen = (open) => {
      sidebar.classList.toggle('is-open', open);
      backdrop.classList.toggle('is-visible', open);
      document.body.classList.toggle('sidebar-open', open);
      sidebarToggles.forEach((toggle) => {
        toggle.setAttribute('aria-expanded', String(open));
        if (toggle.classList.contains('sidebar-toggle')) toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
      });
      sidebar.inert = mobileViewport.matches && !open;
      if (open) sidebar.querySelector('.sidebar-link')?.focus({ preventScroll: true });
    };
    sidebarToggles.forEach((toggle) => toggle.addEventListener('click', () => {
      lastToggle = toggle;
      setOpen(!sidebar.classList.contains('is-open'));
    }));
    backdrop.addEventListener('click', () => setOpen(false));
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && sidebar.classList.contains('is-open')) {
        setOpen(false);
        lastToggle.focus();
      }
    });
    mobileViewport.addEventListener('change', (event) => {
      if (!event.matches) setOpen(false);
      sidebar.inert = event.matches && !sidebar.classList.contains('is-open');
    });
  }

  // Inline form validation for forms marked data-validate. The server still validates
  // everything; this gives earlier, clearer feedback next to each field.
  const formatDate = (value) => {
    const parsed = new Date(`${value}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };

  const isValidatable = (control) => control.willValidate && !['hidden', 'submit', 'button', 'reset'].includes(control.type);

  const applyCustomChecks = (control) => {
    control.setCustomValidity('');
    const form = control.form;
    const trimmed = typeof control.value === 'string' ? control.value.trim() : '';
    if (control.type === 'file') {
      const file = control.files?.[0];
      if (!file) return;
      const accepted = (control.accept || '').split(',').map((item) => item.trim().toLowerCase()).filter(Boolean);
      const extension = `.${file.name.split('.').pop().toLowerCase()}`;
      if (accepted.length && !accepted.includes(extension)) {
        control.setCustomValidity(`Choose a ${accepted.map((item) => item.replace('.', '').toUpperCase()).join(', ')} file.`);
      } else if (control.dataset.maxSize && file.size > Number(control.dataset.maxSize)) {
        control.setCustomValidity(`Files must be ${Math.round(Number(control.dataset.maxSize) / 1048576)} MB or smaller.`);
      }
      return;
    }
    if (control.tagName === 'SELECT' || control.type === 'checkbox' || control.type === 'radio') return;
    if (control.required && control.value !== '' && trimmed === '') {
      control.setCustomValidity('This field is required.');
    } else if (control.minLength > 0 && trimmed !== '' && trimmed.length < control.minLength) {
      control.setCustomValidity(`Use at least ${control.minLength} characters (you have ${trimmed.length}).`);
    } else if (control.dataset.match && form?.elements[control.dataset.match] && control.value && control.value !== form.elements[control.dataset.match].value) {
      control.setCustomValidity(control.dataset.matchMessage || 'The values do not match.');
    }
  };

  const messageFor = (control) => {
    const { validity } = control;
    if (validity.customError) return control.validationMessage;
    if (validity.valueMissing) {
      if (control.type === 'file') return 'Choose a file to upload.';
      if (control.tagName === 'SELECT' || control.type === 'checkbox' || control.type === 'radio') return 'Please select an option.';
      return 'This field is required.';
    }
    if (validity.typeMismatch) return control.type === 'email' ? 'Enter a valid email address, for example name@example.com.' : 'Enter a valid value.';
    if (validity.patternMismatch) return control.dataset.patternMessage || 'Use the requested format.';
    if (validity.tooLong) return `Use ${control.maxLength} characters or fewer.`;
    if (validity.badInput) return control.type === 'number' ? 'Enter a number.' : 'Enter a valid date.';
    if (validity.rangeUnderflow) return control.type === 'date' ? `Choose a date on or after ${formatDate(control.min)}.` : `Enter ${control.min} or more.`;
    if (validity.rangeOverflow) return control.type === 'date' ? `Choose a date on or before ${formatDate(control.max)}.` : `Enter ${control.max} or less.`;
    if (validity.stepMismatch) return 'Enter a whole number.';
    return control.validationMessage;
  };

  const setFieldError = (control, message) => {
    const container = control.closest('[data-field]');
    const errorElement = container?.querySelector('[data-field-error]');
    const describedBy = new Set((control.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
    container?.classList.toggle('has-error', Boolean(message));
    if (message) control.setAttribute('aria-invalid', 'true');
    else control.removeAttribute('aria-invalid');
    if (!errorElement) return;
    errorElement.textContent = message;
    errorElement.hidden = !message;
    if (message) describedBy.add(errorElement.id);
    else describedBy.delete(errorElement.id);
    if (describedBy.size) control.setAttribute('aria-describedby', [...describedBy].join(' '));
    else control.removeAttribute('aria-describedby');
  };

  const validateControl = (control) => {
    applyCustomChecks(control);
    const valid = control.checkValidity();
    setFieldError(control, valid ? '' : messageFor(control));
    return valid;
  };

  const showFormAlert = (form, invalidCount) => {
    let alert = form.querySelector('[data-form-alert]');
    if (!alert) {
      alert = document.createElement('div');
      alert.className = 'form-alert';
      alert.setAttribute('role', 'alert');
      alert.dataset.formAlert = '';
      const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
      if (csrfInput) csrfInput.after(alert);
      else form.prepend(alert);
    }
    alert.innerHTML = '<span class="form-alert-icon" aria-hidden="true">!</span><div><strong>Please check the form</strong><p></p></div>';
    alert.querySelector('p').textContent = invalidCount === 1 ? '1 field needs your attention.' : `${invalidCount} fields need your attention.`;
  };

  const addPasswordToggle = (input) => {
    const wrapper = document.createElement('span');
    wrapper.className = 'password-field';
    input.before(wrapper);
    wrapper.append(input);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'password-toggle';
    toggle.textContent = 'Show';
    toggle.setAttribute('aria-controls', input.id);
    toggle.setAttribute('aria-pressed', 'false');
    toggle.setAttribute('aria-label', 'Show password');
    toggle.addEventListener('click', () => {
      const showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
      toggle.textContent = showing ? 'Show' : 'Hide';
      toggle.setAttribute('aria-pressed', String(!showing));
      toggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    });
    wrapper.append(toggle);
  };

  const addStrengthMeter = (input) => {
    const meter = document.createElement('div');
    meter.className = 'password-strength';
    meter.hidden = true;
    meter.innerHTML = '<span class="password-strength-bar" aria-hidden="true"><span></span><span></span><span></span><span></span></span><span aria-live="polite"></span>';
    const label = meter.querySelector('[aria-live]');
    (input.closest('.password-field') || input).after(meter);
    input.addEventListener('input', () => {
      const value = input.value;
      let score = 0;
      if (value.length >= 8) score += 1;
      if (value.length >= 12) score += 1;
      if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
      if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1;
      const level = value ? (value.length < 8 ? 1 : Math.max(1, score)) : 0;
      meter.hidden = !value;
      meter.dataset.level = String(level);
      label.textContent = ['', 'Weak password', 'Fair password', 'Good password', 'Strong password'][level];
    });
  };

  const enhanceFileInput = (input) => {
    const zone = document.createElement('div');
    zone.className = 'file-drop';
    zone.innerHTML = '<span class="file-drop-icon" aria-hidden="true">↑</span><span class="file-drop-text"><strong>Choose a file</strong><span>or drag and drop it here</span></span>';
    const title = zone.querySelector('strong');
    const hint = zone.querySelector('.file-drop-text > span');
    input.before(zone);
    input.classList.add('file-drop-input');
    zone.append(input);
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      zone.classList.toggle('is-filled', Boolean(file));
      title.textContent = file ? file.name : 'Choose a file';
      hint.textContent = file ? `${(file.size / 1048576).toFixed(file.size < 104858 ? 2 : 1)} MB · click to choose a different file` : 'or drag and drop it here';
    });
    ['dragenter', 'dragover'].forEach((name) => input.addEventListener(name, () => zone.classList.add('is-dragging')));
    ['dragleave', 'drop'].forEach((name) => input.addEventListener(name, () => zone.classList.remove('is-dragging')));
  };

  const addCharacterCounter = (textarea) => {
    const counter = document.createElement('small');
    counter.className = 'field-counter';
    counter.setAttribute('aria-hidden', 'true');
    const update = () => {
      counter.textContent = `${textarea.value.length} / ${textarea.maxLength}`;
      counter.classList.toggle('is-near-limit', textarea.value.length >= textarea.maxLength * 0.9);
    };
    textarea.after(counter);
    textarea.addEventListener('input', update);
    update();
  };

  document.querySelectorAll('form[data-validate]').forEach((form) => {
    form.noValidate = true;
    const controls = () => Array.from(form.elements).filter(isValidatable);
    const conditionalButtons = form.querySelectorAll('[data-require-field]');

    form.querySelectorAll('input[type="password"]').forEach(addPasswordToggle);
    form.querySelectorAll('input[autocomplete="new-password"]:not([data-match])').forEach(addStrengthMeter);
    form.querySelectorAll('input[type="file"]').forEach(enhanceFileInput);
    form.querySelectorAll('textarea[maxlength]').forEach(addCharacterCounter);

    controls().forEach((control) => {
      control.addEventListener('blur', () => {
        if (control.dataset.touched || control.value) {
          control.dataset.touched = 'true';
          validateControl(control);
        }
      });
      const revalidate = () => {
        if (control.getAttribute('aria-invalid') === 'true') validateControl(control);
        // Re-check a confirmation field when the field it must match changes.
        form.querySelectorAll(`[data-match="${control.name}"]`).forEach((other) => {
          if (other.dataset.touched) validateControl(other);
        });
      };
      control.addEventListener('input', revalidate);
      control.addEventListener('change', revalidate);
    });

    form.addEventListener('submit', (event) => {
      if (form.dataset.submitting) {
        event.preventDefault();
        return;
      }
      // Some fields are only required by a particular button, such as a decline reason.
      const skipped = new Set();
      conditionalButtons.forEach((button) => {
        const target = form.elements[button.dataset.requireField];
        if (!target) return;
        target.required = event.submitter === button;
        if (!target.required) {
          skipped.add(target);
          setFieldError(target, '');
        }
      });
      const invalid = controls().filter((control) => !skipped.has(control) && !validateControl(control));
      if (invalid.length) {
        event.preventDefault();
        showFormAlert(form, invalid.length);
        const first = invalid[0];
        first.closest('details')?.setAttribute('open', '');
        first.focus({ preventScroll: true });
        first.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      form.dataset.submitting = 'true';
      // aria-disabled rather than disabled, so the clicked button's name/value is still sent.
      (event.submitter || form.querySelector('[type="submit"]'))?.setAttribute('aria-disabled', 'true');
    });
  });

  // Restore forms when the page is shown again from the back/forward cache.
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    document.querySelectorAll('form[data-validate][data-submitting]').forEach((form) => {
      delete form.dataset.submitting;
      form.querySelectorAll('[aria-disabled="true"]').forEach((button) => button.removeAttribute('aria-disabled'));
    });
  });

  // Server-side errors: link messages to their inputs and move focus to the first one.
  const firstServerError = document.querySelector('form[data-validate] [aria-invalid="true"]');
  document.querySelectorAll('form[data-validate] [aria-invalid="true"]').forEach((control) => {
    const errorElement = control.closest('[data-field]')?.querySelector('[data-field-error]');
    if (errorElement && !(control.getAttribute('aria-describedby') || '').includes(errorElement.id)) {
      control.setAttribute('aria-describedby', `${control.getAttribute('aria-describedby') || ''} ${errorElement.id}`.trim());
    }
  });
  if (firstServerError) {
    firstServerError.closest('details')?.setAttribute('open', '');
    firstServerError.focus({ preventScroll: true });
    firstServerError.scrollIntoView({ block: 'center' });
  }

  const guard = document.querySelector('[data-session-guard]');
  if (!guard) return;

  const idleMilliseconds = Number(guard.dataset.idleTimeout || 1800) * 1000;
  const warningMilliseconds = Number(guard.dataset.warningSeconds || 120) * 1000;
  const warning = document.querySelector('[data-session-warning]');
  const countdown = document.querySelector('[data-session-countdown]');
  const stayButton = document.querySelector('[data-session-stay]');
  const signOutButton = document.querySelector('[data-session-signout]');
  const logoutForm = document.querySelector('[data-logout-form]');
  const csrfToken = logoutForm?.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
  let lastActivity = Date.now();
  let lastHeartbeat = 0;
  let warningWasShown = false;
  let timedOut = false;

  const goToExpiredLogin = () => {
    window.location.assign(guard.dataset.loginUrl || '/login/?expired=1');
  };

  const heartbeat = async () => {
    if (!csrfToken || timedOut) return false;
    try {
      const response = await fetch(guard.dataset.heartbeatUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
      });
      if (response.redirected) {
        goToExpiredLogin();
        return false;
      }
      if (!response.ok) return false;
      lastHeartbeat = Date.now();
      return true;
    } catch (_) {
      return false;
    }
  };

  const signOut = () => {
    if (timedOut) return;
    timedOut = true;
    if (warning) warning.hidden = true;
    if (logoutForm) logoutForm.requestSubmit();
    else goToExpiredLogin();
  };

  const noteActivity = () => {
    lastActivity = Date.now();
    warningWasShown = false;
    if (warning) warning.hidden = true;
    if (Date.now() - lastHeartbeat > 45000) heartbeat();
  };

  ['pointerdown', 'keydown', 'touchstart', 'click'].forEach((eventName) => {
    document.addEventListener(eventName, noteActivity, { passive: true });
  });

  stayButton?.addEventListener('click', async () => {
    lastActivity = Date.now();
    const renewed = await heartbeat();
    if (renewed) {
      warningWasShown = false;
      if (warning) warning.hidden = true;
    } else {
      goToExpiredLogin();
    }
  });
  signOutButton?.addEventListener('click', signOut);

  window.setInterval(() => {
    if (timedOut) return;
    const now = Date.now();
    const idleFor = now - lastActivity;
    const remaining = idleMilliseconds - idleFor;

    if (remaining <= 0) {
      signOut();
      return;
    }

    if (remaining <= warningMilliseconds) {
      if (warning) warning.hidden = false;
      if (!warningWasShown) {
        warningWasShown = true;
        stayButton?.focus({ preventScroll: true });
      }
      if (countdown) {
        const totalSeconds = Math.ceil(remaining / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = String(totalSeconds % 60).padStart(2, '0');
        countdown.textContent = `${minutes}:${seconds}`;
      }
    }

    if (idleFor < 45000 && now - lastHeartbeat > 45000) heartbeat();
  }, 1000);
})();
