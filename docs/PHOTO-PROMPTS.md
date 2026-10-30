# Photo prompts for the sample set

Twelve generated images, reused across the forty requests with a statement. Each is the photo a
customer would attach to a return request. Save each as `app/data/photos/<file>` at about
1024 by 768, JPEG, under 300 KB.

Style line to prepend to every prompt:

> Photorealistic photo taken on a phone by a customer at home, slightly imperfect framing,
> natural indoor light, kitchen counter or living-room floor, no people, no hands, no text,
> no logos, no watermark, no brand names visible.

| File | Shows | Responsible party it supports | Demo request |
|---|---|---|---|
| `kettle-crushed.jpg` | A stainless stovetop kettle on a counter next to its shipping box. The cardboard box is badly crushed on one corner with a long tear. The kettle's handle has snapped off at the joint and lies beside it. The kettle body is otherwise clean and unused. | carrier | RQ-301 |
| `mixer-crushed.jpg` | A boxed stand mixer still in its retail packaging, the retail box dented inward on one side and the outer shipping box split along a seam. A chip of cream-coloured enamel is visible through the dent. | carrier | RQ-307 |
| `blender-base.jpg` | A countertop blender base with the jug removed, photographed from above. A faint dark scorch mark around the drive socket and a few fine black flecks of plastic on the counter. Light wear on the buttons, consistent with two months of normal use. | retailer, with defect flag | RQ-305 |
| `jacket-hem.jpg` | A dark wool jacket laid flat on a bed. Close enough to see the swing tag still attached at the collar, but the hem and cuffs show faint scuffing and a small pulled thread, as if worn several times. | customer, with used flag | |
| `shirt-tags.jpg` | A folded cotton t-shirt in its clear poly bag on a table, bag unopened, tags visible through the plastic. Pristine. | customer | RQ-302 |
| `wrong-color.jpg` | A ceramic casserole dish in bright orange on a counter, next to a printed packing slip whose only readable detail is a small colour swatch that is clearly blue. The slip text itself is out of focus. | retailer, with wrong_item flag | |
| `headphones-sealed.jpg` | A pair of over-ear headphones in a sealed retail box on a doormat inside a front door, the parcel still in its mailer bag, mailer intact and unopened. | unclear | RQ-306 |
| `pan-scratched.jpg` | A non-stick frying pan photographed from above. Deep scratches across the coating in a crosshatch, a small flake of coating missing near the centre. Pan is otherwise clean. | customer, with used flag | |
| `lamp-missing-part.jpg` | A table lamp assembled on a sideboard, but the lampshade is missing. Beside it the open box with foam inserts, one insert visibly empty. | retailer | |
| `mug-cracked.jpg` | A stoneware mug on a counter with a hairline crack running from rim to base, next to its box. The box has light scuffing but no obvious crush damage. | unclear | |
| `boots-sole.jpg` | A pair of leather boots on a hallway floor, one turned to show the sole, where the sole has separated from the upper at the toe. Light dirt on the soles. | retailer, with defect flag | |
| `toaster-sealed.jpg` | A toaster in its retail box, box opened at the top, toaster still wrapped in its protective film with the cable tie on the cord. | customer | |

Notes for whoever generates them:

- The first three and the jacket matter most; they are on stage.
- Keep the same kitchen and the same lighting across the kitchen shots so the set reads as a
  few households, not a catalog.
- No readable text anywhere. The generative engine should get nothing from a label it could not
  get from the photo.
- The `mug-cracked.jpg` and `headphones-sealed.jpg` photos are meant to be ambiguous. Do not
  add damage to the box or the mailer to make them clearer.
