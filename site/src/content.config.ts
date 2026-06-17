import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
import { readdir, readFile } from 'node:fs/promises';
import { resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const blogSchema = z.object({
  title: z.string(),
  description: z.string(),
  locale: z.enum(['zh', 'en']),
  pubDate: z.coerce.date(),
  author: z.string().default('PawBench team'),
  tags: z.array(z.string()).default([]),
  draft: z.boolean().default(false),
});

const blog = defineCollection({
  loader: glob({ pattern: '**/*.{md,mdx}', base: './src/content/blog' }),
  schema: blogSchema,
});

const blogHtml = defineCollection({
  loader: {
    name: 'html-blog-loader',
    load: async ({ store, generateDigest, parseData, config }) => {
      const base = fileURLToPath(new URL('src/content/blog/', config.root));
      let entries: string[] = [];
      try {
        entries = await readdir(base, { recursive: true }) as string[];
      } catch {
        return;
      }
      for (const entry of entries) {
        const normalized = entry.replace(/\\/g, '/');
        if (!normalized.endsWith('.html')) continue;

        const stem = normalized.replace(/\.html$/, '');
        const jsonPath = resolve(base, `${stem}.json`);

        let meta: Record<string, unknown> = {};
        try {
          meta = JSON.parse(await readFile(jsonPath, 'utf-8'));
        } catch {
          // no sidecar JSON — skip this file
          continue;
        }

        const htmlPath = resolve(base, normalized);
        const body = await readFile(htmlPath, 'utf-8');
        const id = normalized;
        const parsedData = await parseData({ id, data: meta });
        store.set({ id, data: parsedData, body, digest: generateDigest(body + JSON.stringify(meta)) });
      }
    },
  },
  schema: blogSchema,
});

export const collections = { blog, blogHtml };
