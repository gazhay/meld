from gi.repository import Gio


def find_menu_item(menu: Gio.Menu, item_id: str) -> int:
    for idx in range(menu.get_n_items()):
        cur_item_id = menu.get_item_attribute_value(idx, "id")
        if cur_item_id is not None and cur_item_id.get_string() == item_id:
            break
    else:
        raise ValueError(f"Menu item with id {item_id} not found")
    return idx


def replace_menu_item(menu: Gio.Menu, item: Gio.MenuItem):
    """Replaces an existing item in GMenu `menu` with `item`

    The items are compared by their `id` attributes, with the
    matching item in `menu` being replaced by the passed `item`.

    If there is no item in `menu` that matches `item`'s `id`
    attribute, a ValueError is raised.
    """
    item_id = item.get_attribute_value("id").get_string()
    idx = find_menu_item(menu, item_id)
    menu.remove(idx)
    menu.insert_item(idx, item)
