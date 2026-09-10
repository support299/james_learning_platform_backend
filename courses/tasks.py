"""Background job for the slideshow pptx import.

Converts an uploaded .pptx into one SlideshowSlide (image) per slide, and —
when the source deck has real "Go to page or object" click interactions set
on shapes (PowerPoint/Impress's native slide-jump action) — carries those
over as real hotspots. Confirmed against a hand-authored deck: python-pptx's
`shape.click_action.target_slide` reads exactly that interaction type, but a
LibreOffice-exported file can have a shape whose click action points at a
relationship id missing from that slide's .rels part — read defensively,
per-shape, so one bad shape doesn't sink the whole import.
"""

import io
import logging
import subprocess
import tempfile
from pathlib import Path

from celery import shared_task
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from pdf2image import convert_from_path
from pptx import Presentation

from .models import Lesson, SlideshowSlide

logger = logging.getLogger(__name__)


@shared_task(name='courses.import_slideshow_pptx', bind=True, max_retries=0, acks_late=True)
def import_slideshow_pptx(self, lesson_id, tmp_relpath):
    """No retry: a bad pptx or a soffice crash is deterministic, so retrying
    just wastes time — the fix is a different file, not another attempt.
    acks_late so a worker dying mid-run (as happened once in production)
    gets the task redelivered to another worker instead of losing it —
    safe since the whole pipeline is idempotent (delete-and-recreate)."""
    try:
        lesson = Lesson.objects.get(pk=lesson_id)
    except Lesson.DoesNotExist:
        logger.warning('Slideshow import for lesson %s: lesson no longer exists', lesson_id)
        default_storage.delete(tmp_relpath)
        return
    pptx_path = default_storage.path(tmp_relpath)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [
                    'soffice', '--headless', '--convert-to', 'pdf',
                    '--outdir', tmp, pptx_path,
                ],
                check=True, timeout=120, capture_output=True,
            )
            pdf_path = Path(tmp) / (Path(pptx_path).stem + '.pdf')
            pages = convert_from_path(str(pdf_path), dpi=150)

            prs = Presentation(pptx_path)
            sw, sh = prs.slide_width, prs.slide_height

            # Draft hotspots keyed by pptx slide order — targets are still
            # pptx slide indices at this point, translated to real row ids
            # once every SlideshowSlide has been created below.
            draft_hotspots = []
            skipped = 0
            for slide in prs.slides:
                spots = []
                for shape in slide.shapes:
                    try:
                        target = shape.click_action.target_slide
                    except KeyError:
                        # A shape whose click action points at a relationship
                        # id missing from this slide's .rels part — a real
                        # LibreOffice pptx-export quirk, confirmed on an
                        # actual file. Drop just this hotspot.
                        skipped += 1
                        continue
                    if target is None:
                        continue
                    spots.append({
                        'x': shape.left / sw,
                        'y': shape.top / sh,
                        'w': shape.width / sw,
                        'h': shape.height / sh,
                        'target': prs.slides.index(target),
                    })
                draft_hotspots.append(spots)
            if skipped:
                logger.warning(
                    'Slideshow import for lesson %s: skipped %d shape(s) '
                    'with a broken click-action relationship', lesson_id, skipped,
                )

            if not Lesson.objects.filter(pk=lesson_id).exists():
                logger.warning(
                    'Slideshow import for lesson %s: lesson was deleted mid-import, aborting', lesson_id,
                )
                return

            lesson.slideshow_slides.all().delete()
            created = []
            for i, page in enumerate(pages):
                buf_name = f'slide_{i}.png'
                slide_obj = SlideshowSlide(lesson=lesson, order=i, hotspots=[])
                buf = io.BytesIO()
                page.save(buf, format='PNG')
                slide_obj.image.save(buf_name, ContentFile(buf.getvalue()), save=False)
                slide_obj.save()
                created.append(slide_obj)

            # Translate pptx-order hotspot targets into the created rows' ids.
            order_to_id = {s.order: s.id for s in created}
            for slide_obj, spots in zip(created, draft_hotspots):
                resolved = [
                    {**spot, 'target': order_to_id[spot['target']]}
                    for spot in spots
                    if spot['target'] in order_to_id
                ]
                if resolved:
                    slide_obj.hotspots = resolved
                    slide_obj.save(update_fields=['hotspots'])

        lesson.import_status = Lesson.ImportStatus.DONE
        lesson.import_error = ''
        lesson.save(update_fields=['import_status', 'import_error'])
    except Exception:
        logger.exception('Slideshow import failed for lesson %s', lesson_id)
        lesson.import_status = Lesson.ImportStatus.FAILED
        lesson.import_error = (
            'Could not process this file — check it is a valid, non-corrupted '
            '.pptx and try again. Full details are in the server logs.'
        )
        lesson.save(update_fields=['import_status', 'import_error'])
    finally:
        default_storage.delete(tmp_relpath)
