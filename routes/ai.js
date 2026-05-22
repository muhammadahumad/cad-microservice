const express = require('express');
const router = express.Router();

router.post('/', async (req, res) => {
  const { prompt, context } = req.body;
  if (!prompt) return res.status(400).json({ error: 'Prompt required' });

  // Build context description for the system prompt
  let contextDescription = '';
  if (context) {
    contextDescription = `
Current project context (use these values exactly where applicable):
- Plot dimensions: ${context.plotWidth || 'unknown'}m wide × ${context.plotDepth || 'unknown'}m deep
- Setback (mandatory empty space around building): ${context.setback || 'unknown'}m on all sides
- Zone: ${context.zone || 'unknown'}
- Plot type: ${context.plotType || 'center'} (center/corner/double-corner)
- Number of floors: ${context.floors || 'unknown'}
- Unit mix (apartments per floor): ${JSON.stringify(context.unitMix || [])}

Maldivian building rules you must follow:
- Maximum column span: 5.5m (structural concrete columns every 3.5-5.5m)
- Stair + Lift core: fixed at back-right corner, approximately 5.5m × 7.0m
- All bedrooms and living rooms must touch an exterior wall (natural ventilation requirement)
- Bathrooms must be adjacent to the stair core (plumbing shaft alignment)
- Minimum room sizes: Living 18m², Kitchen 6m², Master Bedroom 13.5m², other Bedrooms 11m², Bathroom 3m²
- Wall thickness: 0.15m (150mm concrete block)
- Corridors: minimum 2.0m wide
- Plot type affects how many external walls are available for windows:
  - Center plot: only front and rear walls available for windows
  - Corner plot: front + one side available
  - Double-corner: front + both sides available
`;
  }

  const systemMessage = `You are an architectural layout generator for ARQBLD, specialized in Maldivian apartment buildings.
${contextDescription}
Output ONLY a valid JSON object following this exact schema:

{
  "buildableWidth": number (meters, after subtracting setbacks),
  "buildableDepth": number (meters, after subtracting setbacks),
  "setback": number,
  "wallThickness": number (use 0.15),
  "cores": [
    {
      "name": "Stair + Lift + Riser",
      "x": number,
      "y": number,
      "width": number,
      "depth": number,
      "type": "stairs"
    }
  ],
  "rooms": [
    {
      "id": "unique string",
      "name": "descriptive name",
      "minArea": number (square meters),
      "type": "living" | "kitchen" | "bedroom" | "bathroom",
      "priority": number (1=highest),
      "apartmentId": "string (same for rooms in one apartment)",
      "adjacentTo": ["roomId1", "roomId2"]
    }
  ]
}

Rules:
- Place the stair core at the back-right corner position: x = buildableWidth - coreWidth - 0.5, y = buildableDepth - coreDepth - 0.5
- Group rooms by apartmentId
- Living rooms priority 1, kitchens priority 1, bedrooms priority 2, bathrooms priority 3
- Ensure bedrooms and living rooms are placed along exterior walls
- Bathrooms should be near the stair core
- Do NOT wrap the output in markdown code fences. Output ONLY the raw JSON object.`;

  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'Missing API key' });

  try {
    const response = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        model: 'gpt-4o',
        messages: [
          { role: 'system', content: systemMessage },
          { role: 'user', content: prompt }
        ],
        temperature: 0.2,
        max_tokens: 2000
      })
    });

    const data = await response.json();
    let content = data.choices[0].message.content.trim();
    content = content.replace(/^```[a-z]*\s*/i, '').replace(/\s*```$/i, '');

    const layout = JSON.parse(content);
    res.json(layout);
  } catch (err) {
    console.error('AI generation error:', err.message);
    res.status(500).json({ error: 'AI generation failed', detail: err.message });
  }
});

module.exports = router;