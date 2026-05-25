# Ohmsha Manga Guide Style

Guidelines for educational manga comics using the `ohmsha` preset.

## Character Setup

**Always invent original characters.** Do NOT use named characters from existing manga/anime/comic franchises — only role archetypes.

| Role | Archetype | Traits |
|------|-----------|--------|
| Student (Role A) | Curious learner (original character) | Confused, asks basic but crucial questions, represents reader |
| Mentor (Role B) | Knowledgeable guide (original character) | Knowledgeable, patient, uses invented gadgets as technical metaphors |
| Antagonist (Role C, optional) | Skeptic / disruptor (original character) | Represents misunderstanding, or "noise" in the data |

Custom characters: ask the user for role → name mappings (e.g., `Student:Mio, Mentor:Professor Bolt, Antagonist:Glitch`). If the user requests a trademarked character likeness, decline and offer an original substitute.

## Character Reference Sheet Style

For Ohmsha style, use manga/anime style with:
- Exaggerated expressions for educational clarity
- Simple, distinctive silhouettes
- Bright, saturated color palettes
- Chibi/SD (super-deformed) variants for comedic reactions

## Outline Spec Block

Every ohmsha outline must start with:

```markdown
【漫画规格单】
- Language: [Same as input content]
- Style: Ohmsha (Manga Guide), Full Color
- Layout: Vertical Scrolling Comic (竖版条漫)
- Characters: [List character names and roles]
- Character Reference: characters/characters.png
- Page Limit: ≤20 pages
```

## Visual Metaphor Rules (Critical)

**NEVER** create "talking heads" panels. Every technical concept must become:

1. **A tangible gadget/prop** - Something characters can hold, use, demonstrate
2. **An action scene** - Characters doing something that illustrates the concept
3. **A visual environment** - Stepping into a metaphorical space

### Examples

| Concept | Bad (Talking Heads) | Good (Visual Metaphor) |
|---------|---------------------|------------------------|
| Word embeddings | Characters discussing vectors | Mentor pulls out a "Word Vector Compressor" gadget that squeezes books into colored spheres |
| Gradient descent | Explaining math formula | Student rolls a ball through a valley landscape, hunting the lowest point |
| Neural network | Diagram on whiteboard | Characters step into a maze of glowing network nodes |

## Page Title Convention

Avoid AI-style "Title: Subtitle" format. Use narrative descriptions:

- Bad: "Page 3: Introduction to Neural Networks"
- Good: "Page 3: Mio is drowning in a flood of words; Professor Bolt pulls out the Word Vector Compressor"

## Ending Requirements

- NO generic endings ("What will you choose?", "Thanks for reading")
- End with: Technical summary moment OR character achieving a small goal
- Final panel: Sense of accomplishment, not open-ended question

### Good Endings

- Student successfully applies learned concept
- Visual callback to opening problem, now solved
- Mentor gives summary while student demonstrates understanding

### Bad Endings

- "What do you think?" open questions
- "Thanks for reading this tutorial"
- Cliffhanger without resolution

## Layout Preference

Ohmsha style typically uses:
- `webtoon` (vertical scrolling) - Primary choice
- `dense` - For information-heavy sections
- `mixed` - For varied pacing

Avoid `cinematic` and `splash` for educational content.
