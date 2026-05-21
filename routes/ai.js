const express = require('express');
const router = express.Router();

router.post('/', async (req, res) => {
  const { prompt } = req.body;
  if (!prompt) return res.status(400).json({ error: 'Prompt required' });

  const systemMessage = `You are an architectural layout generator for ARQBLD.
Output ONLY a valid JSON object following this exact schema:

{
  "buildableWidth": number,
  "buildableDepth": number,
  "setback": number,
  "wallThickness": number,
  "cores": [ { "name": "Stair + Lift + Riser", "x": number, "y": number, "width": number, "depth": number, "type": "stairs" } ],
  "rooms": [ { "id": "string", "name": "string", "minArea": number, "type": "living|kitchen|bedroom|bathroom", "priority": number, "apartmentId": "string", "adjacentTo": ["roomId"] } ]
}

Rules:
- buildableWidth and buildableDepth are in meters.
- Place cores at back-right corner.
- Rooms grouped by apartmentId.
- Priority: living=1, kitchen=1, bedrooms=2, bathroom=3.
- No extra text.`;

  const apiKey = process.env.OPENAI_API_KEY;
  console.log('OPENAI_API_KEY exists:', !!apiKey);
  if (!apiKey) {
    console.error('Missing OPENAI_API_KEY');
    return res.status(500).json({ error: 'Server configuration error: missing API key' });
  }

  try {
    console.log('Calling OpenAI...');
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

    const responseBody = await response.text();
    console.log('OpenAI status:', response.status);
    console.log('OpenAI response (first 500 chars):', responseBody.substring(0, 500));

    if (!response.ok) {
      console.error('OpenAI error:', responseBody);
      return res.status(500).json({ error: 'OpenAI API error', detail: responseBody });
    }

    const data = JSON.parse(responseBody);
    if (!data.choices || data.choices.length === 0) {
      console.error('No choices in response:', responseBody);
      return res.status(500).json({ error: 'AI did not return any layout. Try a different prompt.' });
    }

    const content = data.choices[0].message.content;
    const layout = JSON.parse(content);
    res.json(layout);
  } catch (err) {
    console.error('AI generation error:', err.message);
    console.error(err.stack);
    res.status(500).json({ error: 'AI generation failed', detail: err.message });
  }
});

module.exports = router;
