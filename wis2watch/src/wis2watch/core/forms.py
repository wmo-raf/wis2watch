from django import forms


class SyncNodeForm(forms.Form):
    node_id = forms.IntegerField()
