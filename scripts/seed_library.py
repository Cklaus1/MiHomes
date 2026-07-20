"""Seed Belle Estate library — insert books and assets from photo inventory."""

import sys
sys.path.insert(0, 'src')

from mihomes.db import get_session
from mihomes.models.book import Book
from mihomes.models.asset import Asset, AssetType
from mihomes.services.book import create_book
from mihomes.services.asset import create_asset
from sqlalchemy import select

PROPERTY = 'belle-estate'
SPACE = 'library'

# (title, author, genre)
BOOKS = [
    # Fiction / Thriller / Mystery
    ("Bright Futures", "Stuart M. Kaminsky", "Fiction"),
    ("Under the Dome", "Stephen King", "Fiction"),
    ("Pattern Recognition", "William Gibson", "Fiction/Sci-Fi"),
    ("The Dark Forest", "Cixin Liu", "Fiction/Sci-Fi"),
    ("Death's End", "Cixin Liu", "Fiction/Sci-Fi"),
    ("Outlander", "Diana Gabaldon", "Fiction/Historical"),
    ("Lord of the Rings", "J.R.R. Tolkien", "Fiction/Fantasy"),
    ("Watchmen", "Alan Moore", "Fiction/Graphic Novel"),
    ("Back Roads", "Tawni O'Dell", "Fiction"),
    ("Ratner's Star", "Don DeLillo", "Fiction"),
    ("High Noon", "Nora Roberts", "Fiction/Romance"),
    ("The Broker", "John Grisham", "Fiction/Thriller"),
    ("A Moveable Feast", "Ernest Hemingway", "Fiction/Memoir"),
    ("The Girl Who Played with Fire", "Stieg Larsson", "Fiction/Thriller"),
    ("Smashed", "Koren Zailckas", "Memoir"),
    ("Atlas Shrugged", "Ayn Rand", "Fiction/Philosophy"),
    ("The Cruising Multihull", "Chris White", "Non-Fiction/Sailing"),
    ("Walden and Other Writings", "Henry David Thoreau", "Classic"),
    ("Ship of Gold in the Deep Blue Sea", "Gary Kinder", "Non-Fiction"),
    ("Persuasion", "Jane Austen", "Fiction/Classic"),
    ("Portrait of a Scotsman", "Evie Dunmore", "Fiction/Historical Romance"),
    ("A Rogue of One's Own", "Evie Dunmore", "Fiction/Historical Romance"),
    ("Bringing Down the Duke", "Evie Dunmore", "Fiction/Historical Romance"),
    ("Prisoner's Dilemma", "Unknown", "Fiction"),
    ("Where Did I Come From?", "Peter Mayle", "Non-Fiction/Children"),
    ("Where Do Babies Come From?", "Peter Mayle", "Non-Fiction/Children"),
    ("Please Excuse My Daughter", "Unknown", "Memoir"),
    ("The God Delusion", "Richard Dawkins", "Non-Fiction"),
    ("David Sedaris: Let's Explore Diabetes with Owls", "David Sedaris", "Humor/Memoir"),
    ("David Sedaris: Me Talk Pretty One Day", "David Sedaris", "Humor/Memoir"),
    # Health / Wellness
    ("New Choices in Natural Healing", "Unknown", "Health"),
    ("Sexy Forever", "Suzanne Somers", "Health"),
    ("Ageless", "Suzanne Somers", "Health"),
    ("The Sexy Years", "Suzanne Somers", "Health"),
    ("Breakthrough", "Suzanne Somers", "Health"),
    ("Magnificent Mind at Any Age", "Daniel G. Amen M.D.", "Health"),
    ("The Spectrum", "Dean Ornish M.D.", "Health"),
    ("Natural Pain Relief", "Unknown", "Health"),
    ("Hormones, Health, and Happiness", "Steven F. Hotze M.D.", "Health"),
    ("When Anger Hurts", "Unknown", "Psychology"),
    ("Natural Cures 2014", "Kevin Trudeau", "Health"),
    ("Juicing for Life", "Unknown", "Health"),
    ("Mind Body Spirit", "Mark Evans", "Health"),
    ("Color Treasury of Herbs and Other Medicinal Plants", "Unknown", "Health"),
    # Parenting / Child Development
    ("Helping Your Anxious Child", "Unknown", "Parenting"),
    ("Your Child's Emotional Health: The Middle Years", "Unknown", "Parenting"),
    ("Nurturing Resilience in Our Children", "Unknown", "Parenting"),
    ("Raising a Self-Disciplined Child", "Robert Brooks & Sam Goldstein", "Parenting"),
    ("The Angry Child", "Tim Murphy & Sam Goldstein", "Parenting"),
    ("How to Talk So Kids Will Listen", "Adele Faber & Elaine Mazlish", "Parenting"),
    ("Parenting Children with ADHD", "Monastra", "Parenting"),
    ("1-2-3 Magic", "Thomas Phelan", "Parenting"),
    ("Managing Difficult Behavior in Children 2-12", "Thomas Phelan", "Parenting"),
    ("NurtureShock", "Po Bronson & Ashley Merryman", "Parenting"),
    ("The Explosive Child", "Ross W. Greene", "Parenting"),
    ("The Highly Sensitive Child", "Elaine N. Aron", "Parenting"),
    ("The Educated Child", "William J. Bennett", "Education"),
    ("Raising Resilient Children", "Brooks & Goldstein", "Parenting"),
    ("Why Do They Act That Way?", "David Walsh", "Parenting"),
    ("Overcoming Dyslexia", "Sally Shaywitz M.D.", "Education"),
    ("Driven to Distraction", "Hallowell & Ratey", "Psychology"),
    ("Answers to Distraction", "Hallowell & Ratey", "Psychology"),
    ("Child Development", "Robert Feldman", "Education"),
    ("Child Development", "Laura Berk", "Education"),
    ("Child Development", "Yussen & Santrock", "Education"),
    ("The Sensitive Child", "Unknown", "Parenting"),
    ("Assessing and Screening Preschoolers", "Lynn", "Education"),
    ("Family Child Care Contracts and Policies", "Tom Copeland", "Business"),
    ("Keys to Developing Your Child", "Pickhardt", "Parenting"),
    ("Guerrilla Learning", "Silvers & Llewellyn", "Education"),
    # Self-Help / Business / Leadership
    ("Drive", "Daniel H. Pink", "Business"),
    ("A Whole New Mind", "Daniel H. Pink", "Business"),
    ("Multiple Intelligences", "Howard Gardner", "Education"),
    ("Emotional Intelligence", "Daniel Goleman", "Psychology"),
    ("The Artist's Way", "Julia Cameron", "Self-Help"),
    ("Good to Great", "Jim Collins", "Business"),
    ("Crunch Point", "Brian Tracy", "Business"),
    ("You Can Be Happy No Matter What", "Richard Carlson", "Self-Help"),
    ("Don't Sweat the Small Stuff", "Richard Carlson", "Self-Help"),
    ("The Five Love Languages", "Gary Chapman", "Self-Help"),
    ("The Purpose-Driven Life", "Rick Warren", "Self-Help"),
    ("Unlimited Power Home Study Course", "Tony Robbins", "Self-Help"),
    ("Unleash the Power Within", "Tony Robbins", "Self-Help"),
    ("Leadership", "Rudolph W. Giuliani", "Business"),
    ("The Generosity Network", "Unknown", "Non-Fiction"),
    ("The Millionaire Real Estate", "Unknown", "Business"),
    ("The Starfish and the Spider", "Brafman & Beckstrom", "Business"),
    ("Eckhart Tolle: Practicing the Power of Now", "Eckhart Tolle", "Spirituality"),
    ("The Sivananda Companion to Yoga", "Unknown", "Health"),
    ("Mind Wide Open", "Unknown", "Psychology"),
    ("Designing Interfaces", "Unknown", "Technology"),
    ("If You Want to Write", "Brenda Ueland", "Self-Help"),
    ("10 Lessons to Transform Your Marriage", "Gottman", "Self-Help"),
    ("Battle for the Mind", "William Sargant", "Psychology"),
    ("The Lexus and the Olive Tree", "Thomas L. Friedman", "Business"),
    ("Who Moved My Cheese", "Spencer Johnson", "Business"),
    ("Successful Manager's Handbook", "Unknown", "Business"),
    ("Essentials of Accounting", "Anthony & Pearlman", "Business"),
    ("Creativity at Work", "DeGraff & Lawrence", "Business"),
    ("The Big Book of Gardening Skills", "Garden Way Publishing", "Gardening"),
    ("Christmas with Southern Living", "Unknown", "Lifestyle"),
    ("Haley's Cleaning Hints", "Unknown", "Home"),
    ("Simpler Living", "Davidson", "Lifestyle"),
    ("New Fix-It-Yourself Manual", "Unknown", "Home"),
    ("Home Wiring and Plumbing", "Unknown", "Home"),
    ("Don't Throw It Out", "Lori Baird", "Home"),
    ("Feng Shui Easy to Use", "Lillian Too", "Home"),
    ("The Purpose-Driven Life", "Rick Warren", "Spirituality"),
    ("Mind in the Making", "Unknown", "Education"),
    ("Vision and Actualization in Academia", "Unknown", "Education"),
    # Travel
    ("Fodor's Amsterdam", "Fodor's", "Travel"),
    ("Frommer's Amsterdam", "Frommer's", "Travel"),
    # Language
    ("Spanish Now!", "Barron's", "Language"),
    ("The University of Chicago Spanish Dictionary", "Unknown", "Language"),
    ("Please Understand Me", "Unknown", "Psychology"),
    ("Now You See It", "Unknown", "Self-Help"),
    # Reference
    ("Dictionary of Theories", "Unknown", "Reference"),
    ("Webster's New Collegiate Dictionary", "Merriam-Webster", "Reference"),
    ("Webster's New Pocket Dictionary", "Unknown", "Reference"),
    ("Webster's Dictionary for Students", "Unknown", "Reference"),
    ("Barron's Dog Bibles: Chihuahuas", "Unknown", "Reference"),
    # Cookbooks
    ("Joy of Cooking", "Unknown", "Cookbook"),
    ("Dr. Atkins Quick and Easy New Diet Cookbook", "Robert Atkins", "Cookbook"),
    ("Sunshine Cocktails", "Ben Reed", "Cookbook"),
    ("365 Snacks Hors D'Oeuvres and Appetizers", "Unknown", "Cookbook"),
    ("The Firehouse Grilling Cookbook", "Joseph T. Bonanno Jr.", "Cookbook"),
    ("Martha Stewart's Healthy Quick Cook", "Martha Stewart", "Cookbook"),
    ("Christmas Cookies", "Unknown", "Cookbook"),
    ("Creative Cooking Fish and Other Seafood", "Culinary Arts Institute", "Cookbook"),
    ("Making Dream Ice Cream", "Unknown", "Cookbook"),
    ("Chevys and Rio Bravo Fresh Mex Cookbook", "Unknown", "Cookbook"),
    ("The Art of the Cocktail", "Philip Collins", "Cookbook"),
    ("Crepes Cookbook", "Unknown", "Cookbook"),
    ("Florence Henderson's Short-Cut Cooking", "Florence Henderson", "Cookbook"),
    ("Quick from Scratch Pasta", "Unknown", "Cookbook"),
    ("Low-Carb Meals in Minutes", "Linda Gassenheimer", "Cookbook"),
    ("Monday to Friday Pasta", "Michele Urvater", "Cookbook"),
    ("Fix-It and Forget-It Lightly", "Unknown", "Cookbook"),
    ("The Pizza Gourmet", "Unknown", "Cookbook"),
    ("Low-Carb Slow Cooker Recipes", "Unknown", "Cookbook"),
]

# (name, asset_type, make, notes)
ASSETS = [
    # Equipment
    ("Paper Shredder", AssetType.EQUIPMENT, None, "White/gray, located in library corner"),
    ("Document Scanner", AssetType.EQUIPMENT, None, "Flatbed scanner on credenza"),
    ("Printer", AssetType.EQUIPMENT, None, "Inkjet/laser printer on credenza"),
    ("GoalZero Power Station", AssetType.EQUIPMENT, "GoalZero", "Portable battery/solar power station, on shelf"),
    ("Red Multi-Drawer Organizer", AssetType.EQUIPMENT, None,
     "Two stacked red metal units with labeled drawers: paint/pencils, label printers, sticky notes, blank cards, "
     "tapes, calculator, dry erase board, flash cards, glues, pens/pencils/erasers, scissors/compass/protractor, "
     "paper clips, staples"),
    ("Label Maker", AssetType.EQUIPMENT, None, "Small label printer, near shredder area"),
    ("Telephone", AssetType.EQUIPMENT, None, "Desk phone with keypad"),
    # Valuables / Decorative
    ("Gargoyle Statue", AssetType.VALUABLE, None, "Large white/cream resin gargoyle, displayed on top bookshelf"),
    ("Wooden Mantel Clock", AssetType.VALUABLE, None,
     "Antique-style mantel clock with Roman numerals, wooden case, on bookshelf"),
    ("Decorative Painted Fan Tray", AssetType.VALUABLE, None,
     "Half-circle painted decorative tray with Western/ranch scene (horses, landscape), black frame"),
    ("Antique Brass Bell Set", AssetType.VALUABLE, None,
     "Set of 4 graduated brass bells on metal rack with wooden base"),
    ("Model Corporate Jet Airplane", AssetType.VALUABLE, None,
     "White decorative model airplane, corporate jet style"),
    ("Decorative Tomahawk/Axe", AssetType.VALUABLE, None,
     "Carved decorative axe/tomahawk, wall-mounted art piece"),
    ("Troll Gnome Figurine", AssetType.VALUABLE, None,
     "Gray/pewter resin troll or gnome figurine, on bookshelf"),
    ("Art Deco Vase Urn", AssetType.VALUABLE, None,
     "Black decorative urn with Art Deco-style design and figure"),
    ("Copper Bronze Pedestal Bowl", AssetType.VALUABLE, None,
     "Antique-style copper/bronze decorative pedestal bowl"),
    ("Decorative Carved Vase", AssetType.VALUABLE, None,
     "Dark carved wood decorative vase with ornate details"),
    ("Antique Brass Lantern", AssetType.VALUABLE, None,
     "Cage-style brass lantern, antique finish"),
    ("Wooden Decorative Box", AssetType.VALUABLE, None,
     "Wooden box with green felt interior and decorative hardware"),
    ("Official Game Hockey Puck Display", AssetType.VALUABLE, None,
     "Official game hockey puck in clear acrylic display case"),
    ("Horseshoe Display", AssetType.VALUABLE, None,
     "White painted horseshoe, mounted on wood as display piece"),
    ("Western Rattlesnake Specimen", AssetType.VALUABLE, None,
     "Preserved Western Rattlesnake specimen in glass dome on wood base with label plate"),
    ("Wooden Trophy Plaque", AssetType.VALUABLE, None,
     "Wooden plaque with gold metal fittings, possibly gun/award mount"),
    ("Decorative Bottle with Ribbon", AssetType.VALUABLE, None,
     "Glass bottle with gold ribbon decoration, on bookshelf"),
]


def main():
    with get_session() as db:
        # Build sets of existing titles/names (case-insensitive)
        existing_books = {b.title.lower() for b in db.execute(select(Book)).scalars()}
        existing_assets = {a.name.lower() for a in db.execute(select(Asset)).scalars()}

        added_books = 0
        skipped_books = 0
        # Track titles inserted this run to handle the duplicate "Child Development" rows
        # and the two "The Purpose-Driven Life" rows within the list itself
        inserted_this_run: set[str] = set()

        for title, author, genre in BOOKS:
            key = title.lower()
            if key in existing_books or key in inserted_this_run:
                print(f"SKIP  book : {title!r} (by {author})")
                skipped_books += 1
                continue
            create_book(db, title, PROPERTY, SPACE, author=author, genre=genre)
            inserted_this_run.add(key)
            print(f"ADD   book : {title!r} (by {author})")
            added_books += 1

        added_assets = 0
        skipped_assets = 0
        for name, atype, make, notes in ASSETS:
            key = name.lower()
            if key in existing_assets:
                print(f"SKIP  asset: {name!r}")
                skipped_assets += 1
                continue
            create_asset(db, name, atype, PROPERTY, space_id_or_slug=SPACE, make=make, notes=notes)
            print(f"ADD   asset: {name!r}")
            added_assets += 1

        print()
        print(f"Done: {added_books} books added, {skipped_books} skipped  |  "
              f"{added_assets} assets added, {skipped_assets} skipped")


if __name__ == '__main__':
    main()
