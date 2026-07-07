type PlaceholderPageProps = {
  title: string;
};

export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <main>
      <h1>{title}</h1>
      <p>Placeholder page.</p>
    </main>
  );
}

