import { useEffect, useState } from 'react';

type Status = 'loading' | 'ready' | 'error';

interface HealthResponse {
  status?: string;
}

export default function App() {
  const [status, setStatus] = useState<Status>('loading');

  useEffect(() => {
    const controller = new AbortController();
    let isMounted = true;

    // Тайм-аут на 5 секунд
    const timeoutId = window.setTimeout(() => {
      controller.abort();
    }, 5000);

    async function checkApi() {
      try {
        const response = await fetch('/api/health', { signal: controller.signal });
        
        if (!response.ok) {
          throw new Error('API request failed');
        }

        const data: unknown = await response.json();

        const isOk =
          typeof data === 'object' &&
          data !== null &&
          (data as HealthResponse).status === 'ok';

        if (!isOk) {
          throw new Error('Unexpected health response');
        }

        if (isMounted) {
          setStatus('ready');
        }
      } catch (error) {
        // Игнорируем отмену запроса при размонтировании
        if (error instanceof DOMException && error.name === 'AbortError' && !controller.signal.aborted) {
          return;
        }
        if (isMounted) {
          setStatus('error');
        }
      } finally {
        window.clearTimeout(timeoutId);
      }
    }

    void checkApi();

    return () => {
      isMounted = false;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, []);

  return (
    <main>
      <p className="eyebrow">Company Memory</p>
      <h1>Компания, которая не забывает</h1>
      <p>Сохраняем опыт команды и находим решения, которые уже помогли.</p>
      <p>Test</p>
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
