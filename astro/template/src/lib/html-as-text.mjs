/*
 * A Markdown plugin for Sätteri, Astro's Markdown processor: HTML written in
 * a post is shown as text, as on Ortszeit, instead of becoming part of the
 * page. `<b>` in a post reads "<b>", and a pasted <script> never runs.
 *
 * Remove it from astro.config.mjs if you want raw HTML in your own posts.
 */
export const htmlAsText = {
  name: 'ortszeit-html-as-text',
  html(node, ctx) {
    ctx.replaceNode(node, { type: 'text', value: node.value });
  },
};
