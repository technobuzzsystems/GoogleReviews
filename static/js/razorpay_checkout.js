'use strict';

async function startRazorpayCheckout(options) {
  const orderUrl = options.orderUrl;
  const verifyUrl = options.verifyUrl;
  const extraBody = options.body || {};
  const onPaid = typeof options.onPaid === 'function' ? options.onPaid : function () { window.location.reload(); };

  const orderRes = await fetch(orderUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(extraBody),
  });
  const order = await orderRes.json().catch(() => ({}));
  if (!orderRes.ok) {
    throw new Error(order.detail || order.error || 'Could not start Razorpay checkout.');
  }
  if (typeof Razorpay !== 'function') {
    throw new Error('Razorpay checkout script did not load.');
  }

  return new Promise((resolve, reject) => {
    const rzp = new Razorpay({
      key: order.key_id,
      amount: order.amount_paise,
      currency: order.currency || 'INR',
      name: order.name || 'TechnoBuzz',
      description: order.description || 'Plan payment',
      order_id: order.order_id,
      prefill: order.prefill || {},
      notes: order.notes || {},
      theme: order.theme || { color: '#00B4D8' },
      handler: async function (response) {
        try {
          const verifyRes = await fetch(verifyUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });
          const data = await verifyRes.json().catch(() => ({}));
          if (!verifyRes.ok) {
            throw new Error(data.detail || data.error || 'Payment verification failed.');
          }
          onPaid(data);
          resolve(data);
        } catch (err) {
          reject(err);
        }
      },
      modal: {
        ondismiss: function () {
          reject(new Error('Payment cancelled.'));
        },
      },
    });
    rzp.on('payment.failed', function (resp) {
      const desc = (resp && resp.error && resp.error.description) || 'Payment failed.';
      reject(new Error(desc));
    });
    rzp.open();
  });
}

document.addEventListener('click', function (event) {
  const copyBtn = event.target.closest('[data-copy-text]');
  if (copyBtn) {
    event.preventDefault();
    const text = copyBtn.getAttribute('data-copy-text') || '';
    if (!text) return;
    const original = copyBtn.textContent;
    const done = function () {
      copyBtn.textContent = 'Copied';
      setTimeout(function () { copyBtn.textContent = original; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {
        window.prompt('Copy this payment link', text);
      });
    } else {
      window.prompt('Copy this payment link', text);
    }
    return;
  }

  const btn = event.target.closest('[data-razorpay-pay]');
  if (!btn) return;
  event.preventDefault();
  if (btn.dataset.busy === '1') return;
  btn.dataset.busy = '1';
  const original = btn.textContent;
  btn.textContent = 'Opening Razorpay…';

  const body = {};
  if (btn.dataset.businessKey) body.business_key = btn.dataset.businessKey;
  if (btn.dataset.bookingId) body.booking_id = Number(btn.dataset.bookingId) || 0;

  startRazorpayCheckout({
    orderUrl: btn.dataset.orderUrl || '/admin/api/payments/order',
    verifyUrl: btn.dataset.verifyUrl || '/admin/api/payments/verify',
    body: body,
    onPaid: function () {
      window.location.reload();
    },
  }).catch(function (err) {
    if (err && err.message !== 'Payment cancelled.') {
      alert(err.message || 'Payment could not be completed.');
    }
  }).finally(function () {
    btn.dataset.busy = '0';
    btn.textContent = original;
  });
});
