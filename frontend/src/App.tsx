import { useEffect, useState } from 'react';

type Status = 'loading' | 'ready' | 'error';

export default function App() {
  const [status, setStatus] = useState<Status>('loading');

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => controller.abort(), 5000);

    async function checkApi() {
      try {
        const response = await fetch('/api/health', { signal: controller.signal });
        if (!response.ok) throw new Error('API request failed');
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('status' in data) || data.status !== 'ok') {
          throw new Error('Unexpected health response');
        }
        if (active) setStatus('ready');
      } catch {
        if (active) setStatus('error');
      } finally {
        window.clearTimeout(timeout);
      }
    }

    void checkApi();
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  return (
    <main>
      <p className="eyebrow">Company Memory</p>
      <h1>Компания, которая не забывает</h1>
      <p>Сохраняем опыт команды и находим решения, которые уже помогли.</p>
      <p><p/>
      <section aria-label="Статус разработки">
        <h2>Основа проекта готова</h2>
        <p>Здесь появятся поиск случаев, история оборудования и подтверждение опыта.</p>
        <p role="status" className={`status ${status}`}>
          {status === 'loading' && 'Проверяем соединение с API…'}
          {status === 'ready' && 'API доступен'}
          {status === 'error' && 'API недоступен. Проверьте запуск backend и обновите страницу.'}
        </p>
      </section>
    </main>
  );
}
