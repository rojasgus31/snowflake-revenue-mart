# Product context

## Register

**Product.** Design serves the data. This is a working analytics tool, not a
showcase surface. It happens to also be read by people evaluating the
engineering behind it, but the way it earns their respect is by being a
credible dashboard, not by being decorated.

## Users and purpose

Two readers, in this order of weight:

**A data or analytics engineer evaluating the work.** They open it for two or
three minutes. They want to know whether the person who built this understands
the data. They will notice if a number is framed misleadingly, if colour means
nothing, or if the hard parts of the pipeline are invisible in the interface.

**A revenue or operations reader.** They want to know where actual revenue
landed against plan, which regions and products are behind, whether orders are
arriving on time, and whether the numbers can be trusted.

The job the interface does: show actual against plan honestly, at the grain the
data actually supports, and make the data-quality position visible rather than
buried.

## The scene

An open-plan room with the lights on, or a conference room with a projector,
or a laptop screen shared on a call: this dashboard gets read in a brightly
lit, shared setting, sitting next to other light documents open in the same
window, such as a spreadsheet, a spec, a ticket. It is also, sometimes, thrown up
on a projector or shared screen for a room of people, where a dark panel is
the one that loses its contrast and its colour under the room's own light.
That forces a light surface: a bright panel is the one that survives being
read by more than one person over someone's shoulder.

## Brand personality

Measured, precise, unhurried. The voice of a good analyst writing a note to a
colleague: states what is true, says what is uncertain, does not oversell.
Confidence comes from precision, never from emphasis.

## Anti-references

- **Startup SaaS marketing.** No gradients, no glass, no oversized rounded
  cards, no hero-metric template, no decorative accent colour.
- **The current design.** Red used decoratively on filter chips, tabs and
  sliders, so that red no longer means anything when a value is genuinely bad.
  Eight equally weighted metric tiles that refuse to say what matters.
- **Generic enterprise BI.** Blue-and-grey Power BI defaults.

## Strategic design principles

1. **Colour carries meaning or it is not used.** Neutrals do the structural
   work. Hue is reserved for the semantics of the data: above or below plan,
   trusted or quarantined. A filter chip is not a semantic state and gets no
   hue.

2. **Do not let the framing lie.** Headline variance is roughly -84%, which
   reads as collapse. The truth is that 187 of 289 product-region-months
   received no orders at all. The interface must make that distinction
   visible rather than let a red badge imply a revenue crash.

3. **Weight follows importance.** The reconciliation balancing exactly and the
   single unmapped-region row are the most interesting facts in the project.
   They currently appear nowhere near the top, or not at all.

4. **Two grains, shown honestly.** Forecast is monthly and regional; delivery
   status is per order. The interface must never imply the two live at the
   same grain, because resolving that conflict is the central design decision
   of the underlying pipeline.

5. **Say what is unknown.** One order has no standard cost, so its margin is
   null rather than zero. Margin totals travel with their coverage. Nulls are
   shown as unknown, never silently rendered as zero.

## Accessibility

- Never encode a state by hue alone. Above and below plan carry sign, position
  or shape in addition to colour.
- Avoid red-green as the only discriminator; a diverging scale must remain
  readable to the most common colour-vision deficiencies.
- Body text meets WCAG AA against the light surface; numerals in tables are
  tabular-figure aligned so columns compare by eye.
